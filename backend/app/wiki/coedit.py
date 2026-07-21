"""Live-session store — the Postgres editing buffer.

The DB is the source of truth for an in-progress *live* edit. There is **one
active session per page** (path-keyed); everyone viewing the page joins it
(the session is the page's live channel — participants include pure viewers;
presence labels editors client-side from their live caret frames, which never
touch this store), and its editors converge on a single server-authoritative
``buffer_text`` + monotonic ``version``.

This module is the *storage* seam only: get-or-create a session, join/leave/
touch participants, and compare-and-swap the buffer. The op/patch channel and
the checkpoint-to-git path (3-way merge through ``commit_and_fan_out``) build on
top of these primitives and live elsewhere. ``base_sha`` is the HEAD the buffer
was last checkpointed against — the merge base for that future checkpoint.

Git stays the source of truth for *committed* pages; this store only holds the
unsaved buffer. See
``Engineering Projects/Agent Wiki Project/design/Co-Editing.md``.
"""

from __future__ import annotations

import logging
from collections.abc import Generator
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.exc import IntegrityError

from app.db.models import CoeditOp, CoeditParticipant, CoeditSession, User
from app.models.wiki import PathMove
from app.db.session import session, try_advisory_xact_lock

log = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Time helpers (match agent_activity: ISO-8601 UTC text, second precision)    #
# --------------------------------------------------------------------------- #


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _iso(ts: datetime) -> str:
    return ts.isoformat()


# --------------------------------------------------------------------------- #
# Row shapes                                                                  #
# --------------------------------------------------------------------------- #


class SessionStatus(str, Enum):
    """Lifecycle state of a co-edit session. Single source of truth for the
    valid `coedit_sessions.status` values; the DB CHECK constraint in
    `app/db/models.py` mirrors these (`str, Enum` so members serialize as their
    string value, matching the `CommentStatus` pattern in `app/models/comment.py`)."""

    ACTIVE = "active"  # accepting ops; exactly one per path (partial unique index)
    CLOSED = "closed"  # finalized after a clean checkpoint; never re-commits


class SessionRow(BaseModel):
    """A row from `coedit_sessions`."""

    id: int
    path: str
    buffer_text: str
    version: int
    checkpointed_version: int
    base_sha: str | None
    status: str
    created_at: str
    updated_at: str
    last_checkpoint_at: str | None


class RebaseWrite(BaseModel):
    """Outcome of a successful ``rebase_onto`` (a raced CAS returns ``None``).

    ``changed`` is False when the buffer already equalled the merged text — only
    ``base_sha`` (and, if checkpointed, ``checkpointed_version``) advanced, no
    version bump. ``session`` is the post-write row.
    """

    model_config = ConfigDict(frozen=True)

    session: SessionRow
    changed: bool


class ParticipantRow(BaseModel):
    """A `coedit_participants` row joined with the user's display name."""

    session_id: int
    user_id: str
    user_display: str
    joined_at: str
    last_seen_at: str
    # NULL until the participant applies an edit op.
    last_edited_at: str | None = None


def _session_row(s: CoeditSession) -> SessionRow:
    return SessionRow(
        id=s.id,
        path=s.path,
        buffer_text=s.buffer_text,
        version=s.version,
        checkpointed_version=s.checkpointed_version,
        base_sha=s.base_sha,
        status=s.status,
        created_at=s.created_at,
        updated_at=s.updated_at,
        last_checkpoint_at=s.last_checkpoint_at,
    )


def _participant_row(p: CoeditParticipant, user_display: str) -> ParticipantRow:
    return ParticipantRow(
        session_id=p.session_id,
        user_id=p.user_id,
        user_display=user_display,
        joined_at=p.joined_at,
        last_seen_at=p.last_seen_at,
        last_edited_at=p.last_edited_at,
    )


# --------------------------------------------------------------------------- #
# Sessions                                                                    #
# --------------------------------------------------------------------------- #


def get_active_session(path: str) -> SessionRow | None:
    """The active session for ``path``, or None if nobody is co-editing it."""
    with session() as s:
        row = s.scalar(
            select(CoeditSession).where(
                CoeditSession.path == path, CoeditSession.status == SessionStatus.ACTIVE.value
            )
        )
        return _session_row(row) if row is not None else None


def blocking_active_session_path(dest: str) -> str | None:
    """Path of an active session at ``dest`` or nested under it, or ``None``.

    Move validation refuses a destination where someone is drafting a
    not-yet-committed page: the session has no file on disk, so the plain
    destination-exists check can't see it (see ``api/wiki.py:/move``)."""
    with session() as s:
        return s.scalar(
            select(CoeditSession.path)
            .where(
                CoeditSession.status == SessionStatus.ACTIVE.value,
                or_(
                    CoeditSession.path == dest,
                    CoeditSession.path.like(dest + "/%"),
                ),
            )
            .limit(1)
        )


def get_session(session_id: int) -> SessionRow | None:
    """Look up a session by id, regardless of status (active or closed)."""
    with session() as s:
        row = s.get(CoeditSession, session_id)
        return _session_row(row) if row is not None else None


def open_session(path: str, *, base_sha: str | None, initial_buffer: str = "") -> SessionRow:
    """Get-or-create the active session for ``path``.

    Returns the existing active session if one is open (``base_sha`` /
    ``initial_buffer`` are ignored then — the live buffer wins). Otherwise
    creates a fresh session seeded from the page's HEAD. Concurrent opens race
    on the partial unique index; the loser re-reads the winner's row.
    """
    with session() as s:
        existing = s.scalar(
            select(CoeditSession).where(
                CoeditSession.path == path, CoeditSession.status == SessionStatus.ACTIVE.value
            )
        )
        if existing is not None:
            return _session_row(existing)
        # Stamp created_at/updated_at in _iso (T-separated, +00:00) rather than
        # letting the space-separated server_default fill them: sessions_due_for_
        # checkpoint compares these against _iso cutoffs, and mixing the two
        # string formats breaks the lexicographic ordering.
        now = _iso(_now())
        fresh = CoeditSession(
            path=path,
            buffer_text=initial_buffer,
            version=0,
            base_sha=base_sha,
            status=SessionStatus.ACTIVE.value,
            created_at=now,
            updated_at=now,
        )
        s.add(fresh)
        try:
            s.flush()
        except IntegrityError:
            # Another opener won the unique-index race — adopt their session.
            s.rollback()
            winner = s.scalar(
                select(CoeditSession).where(
                    CoeditSession.path == path, CoeditSession.status == SessionStatus.ACTIVE.value
                )
            )
            if winner is None:  # pragma: no cover - winner closed in the gap
                raise
            return _session_row(winner)
        return _session_row(fresh)


def set_buffer(session_id: int, *, base_version: int, buffer_text: str) -> SessionRow | None:
    """Compare-and-swap the buffer.

    Bumps ``version`` and replaces ``buffer_text`` only if ``base_version``
    still matches the session's current version (and the session is active).
    Returns the updated row, or ``None`` if the session is gone/closed or the
    version moved underneath the caller (stale — the caller must rebase its
    patch onto the current buffer and retry).

    The compare and the swap are a single conditional ``UPDATE`` so they are
    atomic: two concurrent callers based on the same version can't both win
    (the version predicate is re-checked inside the write, not in Python), so
    there is no lost-update window.
    """
    now = _iso(_now())
    with session() as s:
        sess = s.scalars(
            update(CoeditSession)
            .where(
                CoeditSession.id == session_id,
                CoeditSession.version == base_version,
                CoeditSession.status == SessionStatus.ACTIVE.value,
            )
            .values(buffer_text=buffer_text, version=base_version + 1, updated_at=now)
            .returning(CoeditSession)
            .execution_options(synchronize_session=False)
        ).one_or_none()
        return _session_row(sess) if sess is not None else None


class Change(BaseModel):
    """One range-replacement edit: replace the half-open range ``[from, to)``
    with ``insert``. Offsets are **UTF-16 code units** (JS / CodeMirror string
    positions). ``from`` is a Python keyword, so the field is ``from_`` aliased
    to ``from`` on the wire. This is the shared op shape — the HTTP request
    model (`app/models/coedit.py`) reuses it so FastAPI validates the body."""

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    from_: int = Field(alias="from", ge=0)
    to: int = Field(ge=0)
    insert: str = ""


class OpRow(BaseModel):
    """One logged edit op from `coedit_ops`."""

    seq: int  # the session version this op produced
    author_user_id: str
    client_id: str | None  # the connection that produced it (collab); may be None
    base_version: int
    changes: list[dict[str, Any]]
    created_at: str


class OpsSince(BaseModel):
    """Return of ``ops_since_with_head``: the session's current head version and
    the logged ops in ``(after_version, head]``, read as one snapshot."""

    head_version: int | None  # None if the session no longer exists
    ops: list[OpRow]


def _apply_changes(text: str, changes: list[Change]) -> str:
    """Apply range-replacement ``changes`` to ``text``.

    Structural validity (fields present, ``from``/``to`` are ints ``≥ 0``) is
    guaranteed by the ``Change`` type; this enforces the *semantic* rules that
    need the buffer: each range in-bounds, ranges non-overlapping, and no
    surrogate-pair split. Changes apply right-to-left (highest ``from`` first)
    so an earlier change's length delta never shifts a later change's offsets.

    **Offsets are UTF-16 code units** (JS / CodeMirror positions; an astral char
    like an emoji counts as 2), so we slice in UTF-16 space, not Python code
    points — otherwise a document with an emoji would slice at the wrong place.
    Raises ``ValueError`` on an out-of-bounds range, overlapping ranges, or a
    range that splits a surrogate pair.
    """
    # 2 bytes per UTF-16 code unit → unit offset N is byte offset 2N.
    buf = bytearray(text.encode("utf-16-le"))
    n_units = len(buf) // 2

    # Validate bounds + non-overlap up front — overlapping ranges would apply
    # onto an already-mutated buffer and silently corrupt it.
    ordered = sorted(changes, key=lambda c: (c.from_, c.to))
    prev_to = 0
    for c in ordered:
        if not (0 <= c.from_ <= c.to <= n_units):
            raise ValueError(f"change range [{c.from_},{c.to}) out of bounds for length {n_units}")
        if c.from_ < prev_to:
            raise ValueError(f"overlapping change: [{c.from_},{c.to}) intrudes on a prior range ending at {prev_to}")
        prev_to = c.to

    for c in reversed(ordered):
        buf[2 * c.from_ : 2 * c.to] = c.insert.encode("utf-16-le")
    try:
        return bytes(buf).decode("utf-16-le")
    except UnicodeDecodeError as e:
        raise ValueError("change split a UTF-16 surrogate pair") from e


def rebase_onto(
    session_id: int,
    *,
    base_version: int,
    merged_text: str,
    new_base_sha: str,
    checkpointed: bool,
) -> RebaseWrite | None:
    """Rebase the session buffer onto an external commit under a version CAS.

    Shared by live-rebase (a clean inbound agent commit folded in;
    ``checkpointed=False``) and the checkpoint sync (the committed AI-merged
    result written back; ``checkpointed=True``). This is **not** a co-edit op —
    an agent's change never enters the session op stream / ``coedit_ops``; it's a
    buffer resync driven by a git commit. Participants are told to refetch (a
    ``resync`` frame), not sent an op.

    Returns a ``RebaseWrite``; ``changed`` is False when the buffer already
    equals ``merged_text`` (only ``base_sha`` / ``checkpointed_version`` advance,
    no version bump). When it differs, the buffer is replaced and ``version``
    bumps so any stale in-flight human op is rejected. Returns ``None`` if a
    concurrent op moved the version (caller falls back).
    """
    now = _iso(_now())
    with session() as s:
        # Read the buffer at exactly base_version under the CAS to decide whether
        # it changed (the version predicate guarantees it hasn't moved since).
        current = s.execute(
            select(CoeditSession.buffer_text).where(
                CoeditSession.id == session_id,
                CoeditSession.version == base_version,
                CoeditSession.status == SessionStatus.ACTIVE.value,
            )
        ).scalar_one_or_none()
        if current is None:
            return None
        changed = merged_text != current
        new_version = base_version + 1 if changed else base_version
        values: dict[str, Any] = {"base_sha": new_base_sha, "updated_at": now}
        if changed:
            values["buffer_text"] = merged_text
            values["version"] = new_version
        if checkpointed:
            values["checkpointed_version"] = new_version
            values["last_checkpoint_at"] = now
        row = s.scalars(
            update(CoeditSession)
            .where(
                CoeditSession.id == session_id,
                CoeditSession.version == base_version,
                CoeditSession.status == SessionStatus.ACTIVE.value,
            )
            .values(**values)
            .returning(CoeditSession)
            .execution_options(synchronize_session=False)
        ).one_or_none()
        if row is None:
            return None
        return RebaseWrite(session=_session_row(row), changed=changed)


def apply_op(
    session_id: int,
    *,
    base_version: int,
    changes: list[Change],
    author_user_id: str,
    client_id: str | None = None,
) -> SessionRow | None:
    """Apply an edit op to the buffer and log it, atomically.

    Applies ``changes`` to the buffer as of ``base_version`` and compare-and-
    swaps the version to ``base_version + 1`` — so if another op landed first
    this returns ``None`` (stale; the caller must re-sync and re-apply). On
    success, appends a `coedit_ops` row in the same transaction. Raises
    ``ValueError`` if a change is out of bounds for the current buffer.
    """
    now = _iso(_now())
    with session() as s:
        # Read the buffer at exactly base_version (also confirms active). If the
        # version has moved, the caller is stale — nothing to apply onto.
        current = s.execute(
            select(CoeditSession.buffer_text).where(
                CoeditSession.id == session_id,
                CoeditSession.status == SessionStatus.ACTIVE.value,
                CoeditSession.version == base_version,
            )
        ).scalar_one_or_none()
        if current is None:
            return None
        new_buffer = _apply_changes(current, changes)
        new_version = base_version + 1
        # Re-check the version inside the write (CAS) to close the gap between
        # the read above and this update.
        row = s.scalars(
            update(CoeditSession)
            .where(
                CoeditSession.id == session_id,
                CoeditSession.version == base_version,
                CoeditSession.status == SessionStatus.ACTIVE.value,
            )
            .values(buffer_text=new_buffer, version=new_version, updated_at=now)
            .returning(CoeditSession)
            .execution_options(synchronize_session=False)
        ).one_or_none()
        if row is None:
            return None
        s.add(
            CoeditOp(
                session_id=session_id,
                seq=new_version,
                author_user_id=author_user_id,
                base_version=base_version,
                client_id=client_id,
                op_payload={"changes": [c.model_dump(by_alias=True) for c in changes]},
            )
        )
        return _session_row(row)


def ops_since_with_head(session_id: int, after_version: int) -> OpsSince:
    """The session's current version and its logged ops in ``(after_version,
    head]`` (oldest first), read consistently — for a reconnecting client to
    catch up / rebase. ``head_version`` is None if the session is gone.

    Reads ``version`` first, then bounds the op query to ``seq <= version``, so
    an op committing mid-read can't make the two disagree (it's excluded from
    both): the returned ops always match the returned head, without needing a
    stricter isolation level. Head can still exceed the last op's seq — a
    live-rebase bumps the version without logging an op — which correctly
    signals the client to full-resync rather than replay across the gap.
    """
    with session() as s:
        version = s.scalar(
            select(CoeditSession.version).where(CoeditSession.id == session_id)
        )
        if version is None:
            return OpsSince(head_version=None, ops=[])
        rows = s.scalars(
            select(CoeditOp)
            .where(
                CoeditOp.session_id == session_id,
                CoeditOp.seq > after_version,
                CoeditOp.seq <= version,
            )
            .order_by(CoeditOp.seq.asc())
        ).all()
        return OpsSince(
            head_version=version,
            ops=[
                OpRow(
                    seq=o.seq,
                    author_user_id=o.author_user_id,
                    client_id=o.client_id,
                    base_version=o.base_version,
                    changes=list(o.op_payload.get("changes", [])),
                    created_at=o.created_at,
                )
                for o in rows
            ],
        )


def mark_checkpointed(session_id: int, *, base_sha: str, version: int) -> None:
    """Record that the buffer at ``version`` was committed to git at ``base_sha``.

    Advancing ``checkpointed_version`` to ``version`` is what marks the session
    clean — a later edit bumps ``version`` past it, making it dirty again.
    Conditional UPDATE (only advances) so a slow in-flight checkpoint can't
    regress the watermark past what a faster concurrent one already recorded.
    """
    with session() as s:
        s.execute(
            update(CoeditSession)
            .where(
                CoeditSession.id == session_id,
                CoeditSession.checkpointed_version < version,
            )
            .values(
                base_sha=base_sha,
                checkpointed_version=version,
                last_checkpoint_at=_iso(_now()),
            )
            .execution_options(synchronize_session=False)
        )


def sessions_due_for_checkpoint(
    *, idle_seconds: int, max_interval_seconds: int
) -> list[SessionRow]:
    """Active, *dirty* sessions the periodic worker should checkpoint: either
    idle (no edit for ``idle_seconds``) or overdue (not committed within
    ``max_interval_seconds``, or never). All three compared columns
    (``updated_at``, ``last_checkpoint_at``, ``created_at``) are written in
    ``_iso`` format, so the lexicographic string comparisons are well-ordered.
    """
    now = _now()
    idle_cutoff = _iso(now - timedelta(seconds=idle_seconds))
    overdue_cutoff = _iso(now - timedelta(seconds=max_interval_seconds))
    with session() as s:
        rows = s.scalars(
            select(CoeditSession)
            .where(
                CoeditSession.status == SessionStatus.ACTIVE.value,
                CoeditSession.version > CoeditSession.checkpointed_version,
                or_(
                    # settled: no edit for ``idle_seconds``
                    CoeditSession.updated_at <= idle_cutoff,
                    # overdue: not committed within ``max_interval_seconds`` —
                    # measured from the last checkpoint, or session start if
                    # never checkpointed (so a never-idle session still commits,
                    # but a just-opened one isn't grabbed mid-typing).
                    func.coalesce(
                        CoeditSession.last_checkpoint_at, CoeditSession.created_at
                    )
                    <= overdue_cutoff,
                ),
            )
            .order_by(CoeditSession.updated_at.asc())
        ).all()
        return [_session_row(r) for r in rows]


def last_op_author(session_id: int) -> str | None:
    """The user who applied the most recent op (highest seq), or None if the
    session has no logged ops yet. Used to attribute a checkpoint commit."""
    with session() as s:
        return s.scalars(
            select(CoeditOp.author_user_id)
            .where(CoeditOp.session_id == session_id)
            .order_by(CoeditOp.seq.desc())
            .limit(1)
        ).first()


def close_session(session_id: int) -> None:
    """Mark a session closed, freeing the path for a new active session."""
    with session() as s:
        sess = s.get(CoeditSession, session_id)
        if sess is not None and sess.status != SessionStatus.CLOSED.value:
            sess.status = SessionStatus.CLOSED.value
            sess.updated_at = _iso(_now())


def on_path_moved(moves: list[PathMove]) -> None:
    """Re-key co-edit sessions so a session (and its queued checkpoints, which
    resolve the path through the session row) follows a page move/rename.

    Without this, a session keyed to the old path checkpoints its buffer back
    to a path that no longer exists in git — recreating the page under its
    pre-move name. Exact per-pair re-keys only: sessions are keyed to ``.md``
    files and ``git.move_path`` emits one pair per tracked file, so a folder
    rename is fully covered without prefix matching (which would also re-key
    unmoved siblings on a single cross-folder move). Closed sessions are
    re-keyed too, so their history stays attached to the page.

    Destination collisions (an active session already at ``mv.new``): the
    origin session always wins. Long-lived drafts at the destination block
    the move up front (``blocking_active_session_path`` → 409), so any active
    session still here was opened inside the seconds-wide window since that
    check — typically someone opening the just-moved page before this re-key
    ran. It is superseded (closed); if it managed to collect edits, they stay
    in the closed row's buffer. Each pair runs in a savepoint so a racing
    insert that still trips the active-unique index degrades to a logged skip
    instead of aborting the whole move fan-out.
    """
    if not moves:
        return
    with session() as s:
        for mv in moves:
            try:
                with s.begin_nested():
                    dest = s.scalar(
                        select(CoeditSession).where(
                            CoeditSession.path == mv.new,
                            CoeditSession.status == SessionStatus.ACTIVE.value,
                        )
                    )
                    if dest is not None:
                        if dest.version != dest.checkpointed_version:
                            log.warning(
                                "coedit on_path_moved: superseding young dirty "
                                "session %s at %r; its buffer stays in the "
                                "closed row",
                                dest.id,
                                mv.new,
                            )
                        dest.status = SessionStatus.CLOSED.value
                        dest.updated_at = _iso(_now())
                        s.flush()
                    s.execute(
                        update(CoeditSession)
                        .where(CoeditSession.path == mv.old)
                        .values(path=mv.new)
                    )
            except IntegrityError:
                # A racing open_session won the unique index between our check
                # and the update — same outcome as the dirty-collision skip.
                log.warning(
                    "coedit on_path_moved: lost re-key race for %r -> %r; "
                    "leaving sessions at the old path",
                    mv.old,
                    mv.new,
                )


def close_if_clean(session_id: int) -> bool:
    """Close the session only if it's clean (``version == checkpointed_version``).
    Returns True if it closed.

    Atomic, to avoid orphaning a late edit: after a checkpoint commits, an op can
    still land (the session is ``active`` until this runs) and re-dirty the
    buffer. The conditional ``UPDATE`` closes only when nothing new arrived — if
    an op bumped ``version`` in the window, it matches no row and the session
    stays active, so the periodic scan re-checkpoints the new edit rather than
    sealing it in a closed session.
    """
    with session() as s:
        closed = s.scalars(
            update(CoeditSession)
            .where(
                CoeditSession.id == session_id,
                CoeditSession.status == SessionStatus.ACTIVE.value,
                CoeditSession.version == CoeditSession.checkpointed_version,
            )
            .values(status=SessionStatus.CLOSED.value, updated_at=_iso(_now()))
            .returning(CoeditSession.id)
            .execution_options(synchronize_session=False)
        ).one_or_none()
        return closed is not None


def purge_viewer_sessions(limit: int = 500) -> int:
    """Delete closed sessions that never received an edit op. Returns the count.

    With join-on-landing, every page view mints a session whose buffer is a
    full copy of the page — a closed ``version == 0`` row carries no ops, no
    participants (removed on leave; FK cascade catches stragglers), and nothing
    the op-log or checkpoint dedupe ever references, so it is pure dead weight.
    Runs against *closed* rows only: deleting at the close point instead would
    race a concurrent join into an FK violation, while a closed session is a
    soft state joins already tolerate. Bounded so the periodic scan stays
    cheap; the backlog drains across successive runs.
    """
    with session() as s:
        ids = s.scalars(
            select(CoeditSession.id)
            .where(
                CoeditSession.status == SessionStatus.CLOSED.value,
                CoeditSession.version == 0,
                CoeditSession.checkpointed_version == 0,
            )
            .limit(limit)
        ).all()
        if not ids:
            return 0
        s.execute(
            delete(CoeditSession)
            .where(CoeditSession.id.in_(ids))
            .execution_options(synchronize_session=False)
        )
        return len(ids)


# Namespace for checkpoint advisory-lock keys. The whole DB shares one 64-bit
# advisory keyspace (see triggers/repo.py's _REBUILD_ADVISORY_LOCK), so pack a
# tag into the high 32 bits to keep checkpoint keys in their own band and off
# any bare small-integer key. Assumes session_id < 2**32 (a serial won't reach
# 4 billion).
_CHECKPOINT_LOCK_NS = 0xC0ED
# Cap how long a duplicate checkpoint waits for the in-progress one. Comfortably
# above a normal checkpoint (a git commit is ms; an AI merge is seconds) so a
# waiter still blocks long enough to pick up the committed result and no-op —
# but bounded, so a pathologically slow/hung merge can't pin a waiter (and its
# worker thread) indefinitely. On timeout the waiter skips; the periodic scan
# re-enqueues if the session is still dirty.
_CHECKPOINT_LOCK_TIMEOUT_MS = 30_000


def checkpoint_lock_key(session_id: int) -> int:
    return (_CHECKPOINT_LOCK_NS << 32) | session_id


@contextmanager
def checkpoint_lock(session_id: int) -> Generator[bool]:
    """Serialize checkpoints of one session across concurrent workers.

    Yields True if this caller holds the lock (proceed), False if another worker
    held it past ``_CHECKPOINT_LOCK_TIMEOUT_MS`` (skip — a later trigger/scan
    retries). Different sessions still checkpoint in parallel (the lock is keyed
    on session_id); two workers that both dequeued a checkpoint for the *same*
    session run one at a time, so the loser re-reads a clean/closed session and
    no-ops instead of committing the same buffer twice. Uses a *transaction*-
    scoped advisory lock (auto-released on commit/rollback), so a worker that
    dies mid-checkpoint can't strand it. Chosen over ``SELECT ... FOR UPDATE`` on
    the session row because that row is written by every live ``apply_op`` — a
    row lock held across the checkpoint's (possibly LLM) merge would freeze live
    editing; an abstract advisory lock doesn't. See ``coedit_checkpoint``."""
    with session() as s:
        yield try_advisory_xact_lock(
            s, checkpoint_lock_key(session_id), timeout_ms=_CHECKPOINT_LOCK_TIMEOUT_MS
        )


def rename_path(old_path: str, new_path: str) -> None:
    """Re-point sessions when a page moves (called from the move lifecycle)."""
    with session() as s:
        for sess in s.scalars(
            select(CoeditSession).where(CoeditSession.path == old_path)
        ).all():
            sess.path = new_path


def delete_for_path(path: str) -> None:
    """Drop all sessions for a page (called when the page is deleted).

    Participants cascade via the FK.
    """
    with session() as s:
        for sess in s.scalars(
            select(CoeditSession).where(CoeditSession.path == path)
        ).all():
            s.delete(sess)


# --------------------------------------------------------------------------- #
# Participants                                                                 #
# --------------------------------------------------------------------------- #


def join(session_id: int, user_id: str) -> None:
    """Add ``user_id`` to a session, or refresh their ``last_seen_at``."""
    now = _iso(_now())
    with session() as s:
        existing = s.get(CoeditParticipant, (session_id, user_id))
        if existing is not None:
            existing.last_seen_at = now
        else:
            s.add(
                CoeditParticipant(
                    session_id=session_id,
                    user_id=user_id,
                    joined_at=now,
                    last_seen_at=now,
                )
            )


def touch(session_id: int, user_id: str, *, edited: bool = False) -> None:
    """Refresh a participant's ``last_seen_at`` (presence heartbeat).

    ``edited=True`` (the ``/op`` path) also stamps ``last_edited_at``."""
    with session() as s:
        existing = s.get(CoeditParticipant, (session_id, user_id))
        if existing is not None:
            now = _iso(_now())
            existing.last_seen_at = now
            if edited:
                existing.last_edited_at = now


def leave(session_id: int, user_id: str) -> None:
    """Remove ``user_id`` from a session."""
    with session() as s:
        existing = s.get(CoeditParticipant, (session_id, user_id))
        if existing is not None:
            s.delete(existing)


def list_participants(session_id: int) -> list[ParticipantRow]:
    """All participants of a session, with display names, oldest join first."""
    user_display = func.coalesce(User.name, User.email).label("user_display")
    with session() as s:
        rows = s.execute(
            select(CoeditParticipant, user_display)
            .join(User, User.id == CoeditParticipant.user_id)
            .where(CoeditParticipant.session_id == session_id)
            .order_by(CoeditParticipant.joined_at.asc())
        ).all()
        return [_participant_row(p, disp) for p, disp in rows]
