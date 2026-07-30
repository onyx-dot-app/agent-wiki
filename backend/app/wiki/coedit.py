"""Live-session store — the Postgres bookkeeping for a live co-edit session.

The DB is the source of truth for a session's *lifecycle* (one active session
per page, participants, checkpoint watermark) *and* for its document, which is
the snapshot-plus-update-log pair described below. No process holds a replica:
a `pycrdt.Doc` is built on demand from these rows, used, and dropped (see
`coedit_live.py`). This module never imports `pycrdt` at all — it is pure DB
bookkeeping, on purpose, so the `Doc` thread-affinity constraint stays confined
to `coedit_live.py` and the WS route that drives it.

`coedit_updates` is the durable, replayable log of every applied Yjs update
(this session's analog of the old OT-era `coedit_ops`); `ydoc_snapshot` +
`ydoc_snapshot_seq` on `coedit_sessions` is a point-in-time binary snapshot
of the doc at that seq, set once at session creation (`set_initial_snapshot`,
seeded from the page's HEAD) and advanced by every checkpoint
(`advance_checkpoint`). Together they *are* the document: rebuild a throwaway
`Doc` from the snapshot, replay every update in
`(ydoc_snapshot_seq, ydoc_seq]` from this log onto it, and any process gets the
same result — see `app/wiki/coedit_live.py` and
`app/wiki/coedit_checkpoint.py`. This module never imports `pycrdt` itself
(rebuilding happens in those two); it only stores and serves the bytes.

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

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
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
    document is rebuilt from the snapshot plus this log, not stored here."""

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


class ParticipantExpiry(BaseModel):
    """Roster changes made while expiring stale participants."""

    model_config = ConfigDict(frozen=True)

    changed_session_ids: list[int] = Field(default_factory=list)
    empty_session_ids: list[int] = Field(default_factory=list)


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
    """A row from `coedit_sessions` with the fields a rebuild needs —
    including `ydoc_snapshot`, the blob `SessionRow` deliberately excludes for
    every other (hot-path) caller."""

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

    Pure DB bookkeeping — does not touch (or know about) the document, which
    the caller reaches through ``coedit_live`` once it has this row's id.
    Returns the existing active session's row if one is open, in which case
    ``base_sha`` is ignored: that session's own merge base already reflects its
    history. Concurrent opens race on the partial unique index; the loser
    re-reads the winner's row.
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
    session — call once, on first connect (``_seed_snapshot_sync`` in
    ``app/api/coedit.py``), with ``get_update()`` taken on the same thread that
    built the ``Doc`` (required: it is a PyO3 unsendable type). ``body`` is the
    exact raw text the doc was seeded from — must be exactly what the snapshot
    bytes decode to, since this is the checkpoint engine's diff base with no git
    read to fall back on.

    Conditional on ``ydoc_snapshot IS NULL``, so it is safe to call on any
    connection: a session that already has a snapshot (a checkpoint ran, or
    another connection stamped one) is left alone.

    Returns whether *this* call's snapshot won. Two processes can both observe
    ``ydoc_snapshot IS NULL`` and each seed one; each ``seed_doc_from_markdown``
    call invents its own CRDT lineage (see that function's own docstring), so
    the loser's bytes correspond to nothing durable and no replay could ever
    integrate an update logged against that lineage. The caller must therefore
    drop its own doc on ``False`` and rebuild from whichever snapshot did win —
    which is what ``app/api/coedit.py:ws`` does by simply not keeping one.
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
    author_user_id: str | None  # None for a server-produced update
    client_id: str | None
    update_payload: bytes
    created_at: str


class UpdatesSince(BaseModel):
    """Return of ``updates_since``: the session's current head seq and the
    logged updates in ``(after_seq, head]``, read as one snapshot."""

    model_config = ConfigDict(frozen=True)

    head_seq: int | None  # None if the session no longer exists
    updates: list[UpdateRow]


def has_snapshot(session_id: int) -> bool:
    """Whether the session has its initial snapshot, i.e. is rebuildable.

    A boolean rather than a ``SessionRow`` field: the snapshot is a
    potentially large blob, and every caller of this only needs to know
    whether seeding one is still owed (see ``SessionRow``'s own docstring).
    """
    with session() as s:
        return (
            s.scalars(
                select(CoeditSession.id).where(
                    CoeditSession.id == session_id,
                    CoeditSession.ydoc_snapshot.is_not(None),
                )
            ).first()
            is not None
        )


def apply_update(
    session_id: int,
    *,
    update_bytes: bytes,
    author_user_id: str | None,
    client_id: str | None = None,
) -> int | None:
    """Durably log a Yjs update, returning its assigned seq (or ``None`` if the
    session isn't active).

    ``author_user_id=None`` marks a server-produced update — a live-rebase
    fold of an out-of-band commit has no human author.

    Unlike the OT-era ``apply_op``, there's no version-conflict rejection to
    make: CRDT updates commute, so there is no "based on the wrong version"
    state to reject. Nothing merges the update into a server-side replica
    either, because there isn't one — the log *is* the document. Callers have
    only established that the update is integrable: the WS route validates a
    client's against a scratch ``Doc`` (``coedit_live.validate_update``), and
    the live-rebase produces its own from a rebuild
    (``coedit_live.rebase_delta``) with no route involved at all.

    This just appends the row and advances the watermark, atomically via one
    ``RETURNING`` update.
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

def set_base_sha(session_id: int, base_sha: str) -> bool:
    """Point an active session's merge base at ``base_sha``. True if it moved.

    All that survives of the old ``rebase_onto``. Folding an out-of-band commit
    into a session is now an ordinary logged Yjs update (see
    ``coedit_live.rebase_delta``), which commutes with whatever clients are
    appending — so there is no snapshot to swap, no log to delete, and no
    ``expected_seq`` compare-and-swap to lose. The only durable consequence left
    is which commit the next checkpoint diffs against.
    """
    with session() as s:
        moved = s.scalars(
            update(CoeditSession)
            .where(
                CoeditSession.id == session_id,
                CoeditSession.status == SessionStatus.ACTIVE.value,
            )
            .values(base_sha=base_sha, updated_at=_iso(_now()))
            .returning(CoeditSession.id)
            .execution_options(synchronize_session=False)
        ).one_or_none()
        return moved is not None


def advance_checkpoint(
    session_id: int, *, seq: int, snapshot: bytes, body: str, base_sha: str
) -> None:
    """Record a checkpoint's result — a real commit, or a no-op where the
    doc's content already matched HEAD — moving the snapshot, the
    checkpoint watermark, and the update-log pruning boundary together, in
    one transaction: ``ydoc_snapshot``/``ydoc_snapshot_seq``/
    ``ydoc_snapshot_body`` and ``ydoc_checkpointed_seq`` all advance to
    ``seq``, and every ``coedit_updates`` row with ``seq`` less-or-equal is
    pruned. ``body`` must be the markdown ``snapshot`` reconstructs to —
    content-equal, not byte-equal: the codec normalizes (block terminators
    especially), so ``reconstruct_body`` of the snapshot is what has to match,
    not the author's original bytes. The next checkpoint's diff base comes from
    here, not a git read at ``base_sha`` (see ``ydoc_snapshot_body`` on the
    model).

    The three have to move in lockstep: a checkpoint's snapshot and its pruning
    boundary must always agree, or a later checkpoint's replay-from-snapshot
    would be missing
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

    Process-agnostic: a checkpoint rebuilds its own throwaway ``Doc`` from
    ``ydoc_snapshot`` + the update log (see
    ``app/wiki/coedit_checkpoint.py``), so any worker that dequeues a session
    id from here can act on it directly.
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
    """The user who applied the most recent human update (highest seq), or None
    if the session has no such update yet. Used to attribute a checkpoint
    commit — so server-produced updates (NULL author: a live-rebase fold) are
    skipped rather than costing the commit its attribution."""
    with session() as s:
        return s.scalars(
            select(CoeditUpdate.author_user_id)
            .where(
                CoeditUpdate.session_id == session_id,
                CoeditUpdate.author_user_id.is_not(None),
            )
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

    Returns the ids of any superseded (closed) sessions. Closing the row is the
    whole job: no process holds a live document for a session, so there is
    nothing in memory to evict and no caller has to be told. Callers are free to
    ignore the return value.

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
    """Close an empty session only if it's clean (``ydoc_seq ==
    ydoc_checkpointed_seq``).

    Returns True if it closed. The participant predicate and ``join`` row lock
    prevent a concurrent join from landing in a closed session.

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
                ~select(CoeditParticipant.session_id)
                .where(CoeditParticipant.session_id == session_id)
                .exists(),
            )
            .values(status=SessionStatus.CLOSED.value, updated_at=_iso(_now()))
            .returning(CoeditSession.id)
            .execution_options(synchronize_session=False)
        ).one_or_none()
        return closed is not None


def close_abandoned_sessions() -> list[int]:
    """Close every clean active session that has no participants. Returns their ids.

    ``close_if_clean`` only runs for a session the caller already has in hand —
    the one whose last participant just expired. A session that empties by any
    other route (a participant row removed directly, an FK cascade, a leave
    recorded by an older build) is invisible to that path, and being clean it is
    also skipped by ``sessions_due_for_checkpoint``, so it stays ``active``
    forever: it holds the active-path unique index, so page moves are refused
    (``blocking_active_session_path``), and every new viewer adopts its buffer
    instead of reading HEAD. This is the self-healing sweep for that state —
    same predicate as ``close_if_clean``, applied set-wise rather than to one id.
    """
    with session() as s:
        return list(
            s.scalars(
                update(CoeditSession)
                .where(
                    CoeditSession.status == SessionStatus.ACTIVE.value,
                    CoeditSession.ydoc_seq == CoeditSession.ydoc_checkpointed_seq,
                    ~select(CoeditParticipant.session_id)
                    .where(CoeditParticipant.session_id == CoeditSession.id)
                    .exists(),
                )
                .values(status=SessionStatus.CLOSED.value, updated_at=_iso(_now()))
                .returning(CoeditSession.id)
                .execution_options(synchronize_session=False)
            ).all()
        )


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
# Participants                                                                #
# --------------------------------------------------------------------------- #


def expire_stale_participants(*, stale_seconds: int, limit: int = 500) -> ParticipantExpiry:
    """Remove participants whose shared heartbeat exceeded its grace period."""
    cutoff = _iso(_now() - timedelta(seconds=stale_seconds))
    changed_sessions: set[int] = set()
    with session() as s:
        candidates = s.execute(
            select(CoeditParticipant.session_id, CoeditParticipant.user_id)
            .where(CoeditParticipant.last_seen_at <= cutoff)
            .order_by(CoeditParticipant.last_seen_at.asc())
            .limit(limit)
        ).all()
        for session_id, user_id in candidates:
            participant = s.scalar(
                select(CoeditParticipant)
                .where(
                    CoeditParticipant.session_id == session_id,
                    CoeditParticipant.user_id == user_id,
                )
                .with_for_update()
            )
            if participant is None:
                continue
            if participant.last_seen_at > cutoff:
                continue
            s.delete(participant)
            changed_sessions.add(session_id)
        s.flush()
        nonempty_sessions: set[int] = set()
        if changed_sessions:
            nonempty_sessions = set(
                s.scalars(
                    select(CoeditParticipant.session_id).where(
                        CoeditParticipant.session_id.in_(changed_sessions)
                    )
                ).all()
            )
    return ParticipantExpiry(
        changed_session_ids=sorted(changed_sessions),
        empty_session_ids=sorted(changed_sessions - nonempty_sessions),
    )


def join(session_id: int, user_id: str) -> bool:
    """Join an active session, returning False if it closed during setup."""
    now = _iso(_now())
    with session() as s:
        active = s.scalar(
            select(CoeditSession.id)
            .where(
                CoeditSession.id == session_id,
                CoeditSession.status == SessionStatus.ACTIVE.value,
            )
            .with_for_update()
        )
        if active is None:
            return False
        participant = pg_insert(CoeditParticipant).values(
            session_id=session_id,
            user_id=user_id,
            joined_at=now,
            last_seen_at=now,
        )
        s.execute(
            participant.on_conflict_do_update(
                index_elements=["session_id", "user_id"],
                set_={"last_seen_at": now},
            )
        )
        return True


def touch(session_id: int, user_id: str, *, edited: bool = False) -> bool:
    """Refresh a participant heartbeat, returning False if it expired.

    ``edited=True`` also stamps ``last_edited_at``."""
    with session() as s:
        existing = s.get(CoeditParticipant, (session_id, user_id))
        if existing is not None:
            now = _iso(_now())
            existing.last_seen_at = now
            if edited:
                existing.last_edited_at = now
            return True
        return False


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
