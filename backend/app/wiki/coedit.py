"""Live-session store — the Postgres bookkeeping for a live co-edit session.

The DB is the source of truth for a session's *lifecycle* (one active
session per page, participants, checkpoint watermark) — not for its live
document content, which lives entirely in this process's in-memory
`pycrdt.Doc` (see `coedit_room.py`; `pycrdt.Doc`/`Subscription` are
PyO3-"unsendable" Rust types, so a session's document can only ever be
touched from the process — in practice, the single connection-handling path
— that created it). This module never imports `pycrdt` at all; it is pure DB
bookkeeping, on purpose, so the thread-affinity constraint stays confined to
`coedit_room.py` and the WS route that drives it.

`coedit_updates` is the durable, replayable log of every applied Yjs update
(this session's analog of the old OT-era `coedit_ops`); `ydoc_snapshot` +
`ydoc_snapshot_seq` on `coedit_sessions` is a point-in-time binary snapshot
of the doc at that seq, set once at session creation (`set_initial_snapshot`,
seeded from the page's HEAD) and advanced by every checkpoint
(`advance_checkpoint`). Together they're what let a checkpoint run
anywhere, not just in the process holding a session's live room: rebuild a
throwaway `Doc` from the snapshot, replay every update in
`(ydoc_snapshot_seq, ydoc_seq]` from this log onto it, and the result is
byte-identical to what the live room would have produced — see
`app/wiki/coedit_checkpoint.py`. This module still never imports `pycrdt`
itself (that rebuild happens in the checkpoint engine); it only stores and
serves the bytes.

Git stays the source of truth for *committed* pages; this store only holds
live-session bookkeeping. See
`Engineering Projects/Agent Wiki Project/design/Co-Editing.md`.
"""

from __future__ import annotations

import logging
from collections.abc import Generator
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict
from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.exc import IntegrityError

from app.db.models import CoeditParticipant, CoeditSession, CoeditUpdate, User
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

    ACTIVE = "active"  # accepting updates; exactly one per path (partial unique index)
    CLOSED = "closed"  # finalized after a clean checkpoint; never re-commits


class SessionRow(BaseModel):
    """A row from `coedit_sessions`. Deliberately excludes `ydoc_snapshot`
    (a potentially large blob nothing on the hot path needs) — the live
    document lives in this process's `coedit_room.Room`, not here."""

    id: int
    path: str
    ydoc_seq: int
    ydoc_checkpointed_seq: int
    base_sha: str | None
    status: str
    created_at: str
    updated_at: str
    last_checkpoint_at: str | None


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
        ydoc_seq=s.ydoc_seq,
        ydoc_checkpointed_seq=s.ydoc_checkpointed_seq,
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


class CheckpointSessionRow(BaseModel):
    """A row from `coedit_sessions` with the fields the checkpoint engine
    specifically needs to rebuild a doc without touching any process's live
    room — including `ydoc_snapshot`, the blob `SessionRow` deliberately
    excludes for every other (hot-path) caller."""

    model_config = ConfigDict(frozen=True)

    id: int
    path: str
    status: str
    base_sha: str | None
    ydoc_seq: int
    ydoc_checkpointed_seq: int
    ydoc_snapshot: bytes | None
    ydoc_snapshot_seq: int
    ydoc_snapshot_body: str


def get_session_for_checkpoint(session_id: int) -> CheckpointSessionRow | None:
    """Look up a session by id for the checkpoint engine specifically — see
    `CheckpointSessionRow`. Regardless of status: a closed-but-still-dirty
    session is a real (if rare) case the engine itself decides how to
    handle, not something to hide at the read layer."""
    with session() as s:
        row = s.get(CoeditSession, session_id)
        if row is None:
            return None
        return CheckpointSessionRow(
            id=row.id,
            path=row.path,
            status=row.status,
            base_sha=row.base_sha,
            ydoc_seq=row.ydoc_seq,
            ydoc_checkpointed_seq=row.ydoc_checkpointed_seq,
            ydoc_snapshot=row.ydoc_snapshot,
            ydoc_snapshot_seq=row.ydoc_snapshot_seq,
            ydoc_snapshot_body=row.ydoc_snapshot_body,
        )


def open_session(path: str, *, base_sha: str | None) -> SessionRow:
    """Get-or-create the active session row for ``path``.

    Pure DB bookkeeping — does not touch (or know about) the live document;
    the caller seeds/adopts the in-process room separately
    (``coedit_room.get_or_create_room``) once it has this row's id. Returns
    the existing active session's row if one is open (its live room, if this
    process holds one, wins — ``base_sha`` is ignored then). Concurrent
    opens race on the partial unique index; the loser re-reads the winner's
    row.
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
            ydoc_seq=0,
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


def set_initial_snapshot(session_id: int, snapshot: bytes, body: str) -> bool:
    """Persist the very first ``ydoc_snapshot``/``ydoc_snapshot_body`` for a
    session — call once, right after a process constructs the session's
    room for the first time anywhere (``coedit_room.create_room`` in
    ``app/api/coedit.py:ws``), with ``room.doc.get_update()`` taken on the
    same thread that just built the ``Doc`` (required — see
    ``coedit_room.py``). ``body`` is the exact raw text the room was seeded
    from (the same string passed to ``create_room``) — must be exactly what
    the snapshot bytes decode to, since this is the checkpoint engine's diff
    base with no git read to fall back on.

    Conditional on ``ydoc_snapshot IS NULL`` so this is safe to call every
    time a process creates a room for a session it didn't already know
    about: a session that already has a snapshot (a checkpoint already ran,
    or another process's connection already stamped one) leaves it alone —
    only the very first room, for a brand-new session, ever actually
    writes here.

    Returns whether *this* call's snapshot actually won (persisted). Two
    processes can both observe ``ydoc_snapshot IS NULL`` and each seed
    their own room independently — each ``seed_doc_from_markdown`` call
    invents its own CRDT lineage (see that function's own docstring), so
    the loser's freshly-built room is now on a lineage nothing durable
    corresponds to: no future checkpoint replay could ever integrate an
    update logged against it (confirmed in review). The caller must check
    this and, on ``False``, discard its own room in favor of rehydrating
    from whichever snapshot *did* win — see ``app/api/coedit.py:ws``.
    """
    with session() as s:
        # .returning(...).one_or_none() (not .rowcount) to detect whether
        # the conditional UPDATE matched — matches this module's other
        # conditional-UPDATE call sites (e.g. close_if_clean, advance_
        # checkpoint), and sidesteps a basedpyright strict-mode gap:
        # SQLAlchemy's plain Result.rowcount isn't typed on the generic
        # Result[Any] this execute() returns.
        row = s.scalars(
            update(CoeditSession)
            .where(CoeditSession.id == session_id, CoeditSession.ydoc_snapshot.is_(None))
            .values(ydoc_snapshot=snapshot, ydoc_snapshot_seq=0, ydoc_snapshot_body=body)
            .returning(CoeditSession.id)
        ).one_or_none()
        return row is not None


class UpdateRow(BaseModel):
    """One logged Yjs update from `coedit_updates`."""

    model_config = ConfigDict(frozen=True)

    seq: int
    author_user_id: str
    client_id: str | None
    update_payload: bytes
    created_at: str


class UpdatesSince(BaseModel):
    """Return of ``updates_since``: the session's current head seq and the
    logged updates in ``(after_seq, head]``, read as one snapshot."""

    model_config = ConfigDict(frozen=True)

    head_seq: int | None  # None if the session no longer exists
    updates: list[UpdateRow]


def apply_update(
    session_id: int, *, update_bytes: bytes, author_user_id: str, client_id: str | None = None
) -> int | None:
    """Durably log an already-applied Yjs update, returning its assigned
    seq (or ``None`` if the session isn't active).

    Unlike the OT-era ``apply_op``, there's no version-conflict rejection:
    CRDT merges are commutative, and the merge itself already happened at
    the ``pycrdt.Doc`` level (``handle_sync_message``, in the WS route)
    *before* this is ever called. This just durably logs the update and
    advances the watermark, atomically via one ``RETURNING`` update.
    """
    now = _iso(_now())
    with session() as s:
        new_seq = s.scalars(
            update(CoeditSession)
            .where(CoeditSession.id == session_id, CoeditSession.status == SessionStatus.ACTIVE.value)
            .values(ydoc_seq=CoeditSession.ydoc_seq + 1, updated_at=now)
            .returning(CoeditSession.ydoc_seq)
            .execution_options(synchronize_session=False)
        ).one_or_none()
        if new_seq is None:
            return None
        s.add(
            CoeditUpdate(
                session_id=session_id,
                seq=new_seq,
                author_user_id=author_user_id,
                client_id=client_id,
                update_payload=update_bytes,
            )
        )
        return new_seq


def updates_since(session_id: int, after_seq: int) -> UpdatesSince:
    """The session's current head seq and its logged updates in
    ``(after_seq, head]`` (oldest first), read consistently — for a
    reconnecting client to catch up. ``head_seq`` is None if the session is
    gone."""
    with session() as s:
        seq = s.scalar(select(CoeditSession.ydoc_seq).where(CoeditSession.id == session_id))
        if seq is None:
            return UpdatesSince(head_seq=None, updates=[])
        rows = s.scalars(
            select(CoeditUpdate)
            .where(
                CoeditUpdate.session_id == session_id,
                CoeditUpdate.seq > after_seq,
                CoeditUpdate.seq <= seq,
            )
            .order_by(CoeditUpdate.seq.asc())
        ).all()
        return UpdatesSince(
            head_seq=seq,
            updates=[
                UpdateRow(
                    seq=u.seq,
                    author_user_id=u.author_user_id,
                    client_id=u.client_id,
                    update_payload=u.update_payload,
                    created_at=u.created_at,
                )
                for u in rows
            ],
        )


def rebase_onto(
    session_id: int,
    *,
    new_base_sha: str,
    snapshot: bytes,
    body: str,
    expected_seq: int,
    checkpointed: bool,
) -> SessionRow | None:
    """Record that the session's document was rebased onto ``new_base_sha``
    — either a live-rebase re-seed (an out-of-band agent/ingest commit
    folded in by fully re-seeding the in-process room from the 3-way-merged
    text and pushing a full resync, see ``coedit_rebase.py``) or the merged
    result a checkpoint just committed.

    Bumps ``ydoc_seq`` by one (the rebase itself counts as a seq-advancing
    event) and, since a rebase replaces the document wholesale rather than
    applying an incremental delta, clears ``coedit_updates`` for this
    session — the pre-rebase log no longer corresponds to anything a
    reconnecting client could meaningfully replay onto the post-rebase doc,
    so catch-up must start clean from the new seq rather than attempt to
    replay through the discontinuity.

    ``expected_seq`` is a CAS: the ``ydoc_seq`` the caller observed when it
    read the doc it built ``snapshot``/``body`` from (``room_body`` in
    ``coedit_rebase.py``). The merge/snapshot-build happens across several
    ``await``s, during which the event loop is free to run ``_recv_loop``
    for a genuine local edit — that edit lands in ``coedit_updates`` *and*
    bumps ``ydoc_seq`` before this call ever runs. Rebasing unconditionally
    in that window would delete that edit's log row and reseed the room to
    a snapshot that never saw it either — silently erased, no fallback (a
    real bug, caught in review; the OT-era ``rebase``'s own
    ``base_version`` CAS existed for exactly this). Conditioning the update
    on ``ydoc_seq == expected_seq`` makes the whole operation a no-op
    (returns ``None``, same as a closed session) when a concurrent edit
    landed — the caller skips this rebase and leaves the room's own edit
    intact; the fold-in the skipped rebase would have applied still
    reaches the page via the checkpoint engine's own merge.

    ``snapshot``/``body`` — a throwaway ``Doc`` seeded from the rebased
    text, its ``get_update()`` bytes and the text itself — must move in
    lockstep with that clear: ``ydoc_snapshot``/``ydoc_snapshot_seq``/
    ``ydoc_snapshot_body`` advance to the new ``ydoc_seq`` in the same
    update. Skipping the snapshot advance was a real bug (caught in
    review): the checkpoint engine never touches this room's live ``Doc``,
    only ``(ydoc_snapshot, coedit_updates)`` (see ``coedit_checkpoint.py``)
    — with the log cleared but the snapshot left pointing at its
    pre-rebase seq, a later checkpoint would rebuild from that stale
    snapshot plus an empty log, silently dropping every edit made since
    the snapshot was last advanced. ``body`` specifically (not a git read
    at ``new_base_sha``) matters here because a live-rebase's merged text
    has no corresponding git commit at all — the merge only ever happens
    in memory, so ``new_base_sha`` (the out-of-band commit that triggered
    the rebase) does *not* decode to ``body`` on its own; a caller that
    passes the wrong ``body`` here corrupts every future checkpoint's diff
    base for this session (also caught in review).

    Takes ``checkpoint_lock`` for the same reason ``checkpoint_session``
    and the WS route's rehydrate path do: every other reader/writer of
    ``(ydoc_snapshot, ydoc_snapshot_seq, coedit_updates)`` takes it so
    those three fields are always read/written as one consistent unit —
    without this function also taking it, a rehydrating reader's own two
    separate reads (``get_session_for_checkpoint`` then ``updates_since``)
    could still land with this call's delete-all-then-advance in between
    them, reading a pre-rebase snapshot alongside a post-rebase (already-
    pruned) update log — stale *and* on the wrong lineage (confirmed in
    review, with a repro). Returns ``None`` (same as a CAS miss) if the
    lock can't be acquired within its own timeout, rather than blocking a
    live-rebase indefinitely on it — the caller
    (``coedit_rebase.rebase_session``) already treats a ``None`` return as
    ``RACED`` and (as of this fix) retries.
    """
    now = _iso(_now())
    with checkpoint_lock(session_id, timeout_ms=_REBASE_LOCK_TIMEOUT_MS) as acquired:
        if not acquired:
            return None
        return _rebase_onto_locked(
            session_id,
            new_base_sha=new_base_sha,
            snapshot=snapshot,
            body=body,
            expected_seq=expected_seq,
            checkpointed=checkpointed,
            now=now,
        )


def _rebase_onto_locked(
    session_id: int,
    *,
    new_base_sha: str,
    snapshot: bytes,
    body: str,
    expected_seq: int,
    checkpointed: bool,
    now: str,
) -> SessionRow | None:
    with session() as s:
        values: dict[str, Any] = {
            "base_sha": new_base_sha,
            "updated_at": now,
            "ydoc_seq": CoeditSession.ydoc_seq + 1,
        }
        row = s.scalars(
            update(CoeditSession)
            .where(
                CoeditSession.id == session_id,
                CoeditSession.status == SessionStatus.ACTIVE.value,
                CoeditSession.ydoc_seq == expected_seq,
            )
            .values(**values)
            .returning(CoeditSession)
            .execution_options(synchronize_session=False)
        ).one_or_none()
        if row is None:
            return None
        row.ydoc_snapshot = snapshot
        row.ydoc_snapshot_seq = row.ydoc_seq
        row.ydoc_snapshot_body = body
        if checkpointed:
            row.ydoc_checkpointed_seq = row.ydoc_seq
            row.last_checkpoint_at = now
        s.execute(delete(CoeditUpdate).where(CoeditUpdate.session_id == session_id))
        return _session_row(row)


def advance_checkpoint(
    session_id: int, *, seq: int, snapshot: bytes, body: str, base_sha: str
) -> None:
    """Record a checkpoint's result — a real commit, or a no-op where the
    doc's content already matched HEAD — moving the snapshot, the
    checkpoint watermark, and the update-log pruning boundary together, in
    one transaction: ``ydoc_snapshot``/``ydoc_snapshot_seq``/
    ``ydoc_snapshot_body`` and ``ydoc_checkpointed_seq`` all advance to
    ``seq``, and every ``coedit_updates`` row with ``seq`` less-or-equal is
    pruned. ``body`` must be exactly what ``snapshot`` decodes to — the
    next checkpoint's diff base comes from here, not a git read at
    ``base_sha`` (see ``ydoc_snapshot_body`` on the model).

    The three have to move in lockstep — unlike ``rebase_onto``'s
    unconditional clear (correct only for a rebase, which replaces the doc
    wholesale so the *entire* pre-rebase log is meaningless regardless of
    seq), a checkpoint's snapshot and its pruning boundary must always
    agree, or a later checkpoint's replay-from-snapshot would be missing
    updates between the (stale) snapshot and the (already-pruned) log —
    exactly the class of bug this function exists to make structurally
    impossible: there is no code path that prunes without also advancing
    the snapshot to the same seq.

    Conditional on ``ydoc_checkpointed_seq < seq`` so a slow in-flight
    checkpoint can't clobber a faster concurrent one's more-advanced state
    — belt-and-suspenders alongside ``coedit.checkpoint_lock``'s own
    per-session serialization, not a substitute for it (matches the old
    ``mark_checkpointed``'s regression guard, which this replaces —
    snapshot advancement was never optional here, so there's no longer a
    narrower "just advance the watermark" operation to keep around).
    """
    now = _iso(_now())
    with session() as s:
        # .returning(...).one_or_none() (not .rowcount) to detect whether the
        # conditional UPDATE matched — matches this module's other
        # conditional-UPDATE call sites (e.g. close_if_clean), and sidesteps
        # a basedpyright strict-mode gap: SQLAlchemy's plain Result.rowcount
        # isn't typed on the generic Result[Any] this execute() returns.
        updated_id = s.scalars(
            update(CoeditSession)
            .where(CoeditSession.id == session_id, CoeditSession.ydoc_checkpointed_seq < seq)
            .values(
                ydoc_snapshot=snapshot,
                ydoc_snapshot_seq=seq,
                ydoc_snapshot_body=body,
                ydoc_checkpointed_seq=seq,
                base_sha=base_sha,
                last_checkpoint_at=now,
                updated_at=now,
            )
            .returning(CoeditSession.id)
            .execution_options(synchronize_session=False)
        ).one_or_none()
        if updated_id is not None:
            s.execute(
                delete(CoeditUpdate).where(
                    CoeditUpdate.session_id == session_id, CoeditUpdate.seq <= seq
                )
            )


def sessions_due_for_checkpoint(
    *, idle_seconds: int, max_interval_seconds: int
) -> list[SessionRow]:
    """Active, *dirty* sessions the periodic scan should checkpoint: either
    idle (no edit for ``idle_seconds``) or overdue (not committed within
    ``max_interval_seconds``, or never). All three compared columns
    (``updated_at``, ``last_checkpoint_at``, ``created_at``) are written in
    ``_iso`` format, so the lexicographic string comparisons are well-ordered.

    Process-agnostic: a checkpoint no longer needs the process holding a
    session's live room (it rebuilds its own throwaway ``Doc`` from
    ``ydoc_snapshot`` + the update log — see
    ``app/wiki/coedit_checkpoint.py``), so any worker that dequeues a
    session id from here can act on it directly, regardless of which
    process (if any) currently holds that session's room.
    """
    now = _now()
    idle_cutoff = _iso(now - timedelta(seconds=idle_seconds))
    overdue_cutoff = _iso(now - timedelta(seconds=max_interval_seconds))
    with session() as s:
        rows = s.scalars(
            select(CoeditSession)
            .where(
                CoeditSession.status == SessionStatus.ACTIVE.value,
                CoeditSession.ydoc_seq > CoeditSession.ydoc_checkpointed_seq,
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


def last_update_author(session_id: int) -> str | None:
    """The user who applied the most recent update (highest seq), or None if
    the session has no logged updates yet. Used to attribute a checkpoint
    commit."""
    with session() as s:
        return s.scalars(
            select(CoeditUpdate.author_user_id)
            .where(CoeditUpdate.session_id == session_id)
            .order_by(CoeditUpdate.seq.desc())
            .limit(1)
        ).first()


def close_session(session_id: int) -> None:
    """Mark a session closed, freeing the path for a new active session."""
    with session() as s:
        sess = s.get(CoeditSession, session_id)
        if sess is not None and sess.status != SessionStatus.CLOSED.value:
            sess.status = SessionStatus.CLOSED.value
            sess.updated_at = _iso(_now())


def on_path_moved(moves: list[PathMove]) -> list[int]:
    """Re-key co-edit sessions so a session (and its queued checkpoints, which
    resolve the path through the session row) follows a page move/rename.

    Without this, a session keyed to the old path checkpoints its document
    back to a path that no longer exists in git — recreating the page under
    its pre-move name. Exact per-pair re-keys only: sessions are keyed to
    ``.md`` files and ``git.move_path`` emits one pair per tracked file, so
    a folder rename is fully covered without prefix matching (which would
    also re-key unmoved siblings on a single cross-folder move). Closed
    sessions are re-keyed too, so their history stays attached to the page.

    Destination collisions (an active session already at ``mv.new``): the
    origin session always wins. Long-lived drafts at the destination block
    the move up front (``blocking_active_session_path`` → 409), so any active
    session still here was opened inside the seconds-wide window since that
    check — typically someone opening the just-moved page before this re-key
    ran. It is superseded (closed); if it managed to collect edits, they stay
    in the closed row's history.

    Returns the ids of any superseded (closed) sessions — this module is
    deliberately pure DB bookkeeping with no ``pycrdt`` import (see
    ``app/wiki/coedit_room.py``'s own module docstring), so it can't evict
    a superseded session's in-memory room itself; the caller
    (``app/wiki/notify.py``) does that via ``coedit_room.evict_if_local``
    for each returned id. Left un-evicted, a superseded room would pin its
    ``Doc``/``Awareness``/``TouchedTracker`` in its owning process's memory
    forever — nothing else would ever call ``coedit_room.close_room`` for
    it once its session row is already closed here (confirmed in review).
    Each pair runs in a savepoint so a racing insert that still trips the
    active-unique index degrades to a logged skip instead of aborting the
    whole move fan-out.
    """
    if not moves:
        return []
    superseded_ids: list[int] = []
    with session() as s:
        for mv in moves:
            # Not appended to superseded_ids until after the try/except
            # below succeeds — the nested transaction can still roll back
            # on IntegrityError, and this plain Python list wouldn't roll
            # back along with it.
            dest_id: int | None = None
            try:
                with s.begin_nested():
                    dest = s.scalar(
                        select(CoeditSession).where(
                            CoeditSession.path == mv.new,
                            CoeditSession.status == SessionStatus.ACTIVE.value,
                        )
                    )
                    if dest is not None:
                        if dest.ydoc_seq != dest.ydoc_checkpointed_seq:
                            log.warning(
                                "coedit on_path_moved: superseding young dirty "
                                "session %s at %r; its history stays in the "
                                "closed row",
                                dest.id,
                                mv.new,
                            )
                        dest.status = SessionStatus.CLOSED.value
                        dest.updated_at = _iso(_now())
                        dest_id = dest.id
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
                continue
            if dest_id is not None:
                superseded_ids.append(dest_id)
    return superseded_ids


def close_if_clean(session_id: int) -> bool:
    """Close the session only if it's clean (``ydoc_seq ==
    ydoc_checkpointed_seq``). Returns True if it closed.

    Atomic, to avoid orphaning a late edit: after a checkpoint commits, an
    update can still land (the session is ``active`` until this runs) and
    re-dirty the doc. The conditional ``UPDATE`` closes only when nothing
    new arrived — if an update bumped ``ydoc_seq`` in the window, it
    matches no row and the session stays active, so the periodic scan
    re-checkpoints the new edit rather than sealing it in a closed session.
    """
    with session() as s:
        closed = s.scalars(
            update(CoeditSession)
            .where(
                CoeditSession.id == session_id,
                CoeditSession.status == SessionStatus.ACTIVE.value,
                CoeditSession.ydoc_seq == CoeditSession.ydoc_checkpointed_seq,
            )
            .values(status=SessionStatus.CLOSED.value, updated_at=_iso(_now()))
            .returning(CoeditSession.id)
            .execution_options(synchronize_session=False)
        ).one_or_none()
        return closed is not None


def purge_viewer_sessions(limit: int = 500) -> int:
    """Delete closed sessions that never received an update. Returns the count.

    With join-on-landing, every page view mints a session — a closed
    ``ydoc_seq == 0`` row carries no updates, no participants (removed on
    leave; FK cascade catches stragglers), and nothing the update log or
    checkpoint dedupe ever references, so it is pure dead weight. Runs
    against *closed* rows only: deleting at the close point instead would
    race a concurrent join into an FK violation, while a closed session is a
    soft state joins already tolerate. Bounded so the periodic scan stays
    cheap; the backlog drains across successive runs.
    """
    with session() as s:
        ids = s.scalars(
            select(CoeditSession.id)
            .where(
                CoeditSession.status == SessionStatus.CLOSED.value,
                CoeditSession.ydoc_seq == 0,
                CoeditSession.ydoc_checkpointed_seq == 0,
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
# rebase_onto's own use of checkpoint_lock — deliberately much shorter. Its
# contention is a fast, momentary Doc-mutation race (not a slow AI merge, the
# scenario the timeout above is tuned for), and it's retried a bounded few
# times on a RACED outcome — waiting the full 30s on each retry would tie up a
# shared asyncio.to_thread worker for minutes under contention (caught in
# review). See checkpoint_lock's own docstring.
_REBASE_LOCK_TIMEOUT_MS = 3_000


def checkpoint_lock_key(session_id: int) -> int:
    return (_CHECKPOINT_LOCK_NS << 32) | session_id


@contextmanager
def checkpoint_lock(session_id: int, *, timeout_ms: int | None = None) -> Generator[bool]:
    """Serialize checkpoints of one session across concurrent workers.

    Yields True if this caller holds the lock (proceed), False if another worker
    held it past ``timeout_ms`` (default ``_CHECKPOINT_LOCK_TIMEOUT_MS`` — skip,
    a later trigger/scan retries). Different sessions still checkpoint in
    parallel (the lock is keyed on session_id); two workers that both dequeued a
    checkpoint for the *same* session run one at a time, so the loser re-reads a
    clean/closed session and no-ops instead of committing the same document
    twice. Uses a *transaction*-scoped advisory lock (auto-released on
    commit/rollback), so a worker that dies mid-checkpoint can't strand it.
    Chosen over ``SELECT ... FOR UPDATE`` on the session row because that row is
    written by every live ``apply_update`` — a row lock held across the
    checkpoint's (possibly LLM) merge would freeze live editing; an abstract
    advisory lock doesn't. See ``coedit_checkpoint``.

    ``timeout_ms`` override: ``coedit_rebase.rebase_onto``'s own use of this
    lock isn't waiting out a slow AI merge (a checkpoint's own scenario, which
    is what ``_CHECKPOINT_LOCK_TIMEOUT_MS`` is tuned for) — it's guarding a
    fast, momentary Doc-mutation race, and (as of a recent fix) gets retried a
    bounded few times on ``RACED``. Waiting the full 30s on each of those
    retries would tie up a shared ``asyncio.to_thread`` worker for minutes
    under lock contention (caught in review); a caller with a narrower,
    faster-to-detect contention window should pass a shorter one.
    """
    with session() as s:
        yield try_advisory_xact_lock(
            s,
            checkpoint_lock_key(session_id),
            timeout_ms=timeout_ms if timeout_ms is not None else _CHECKPOINT_LOCK_TIMEOUT_MS,
        )


def rename_path(old_path: str, new_path: str) -> None:
    """Re-point sessions when a page moves (called from the move lifecycle)."""
    with session() as s:
        for sess in s.scalars(
            select(CoeditSession).where(CoeditSession.path == old_path)
        ).all():
            sess.path = new_path


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

    ``edited=True`` also stamps ``last_edited_at``."""
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
