"""Co-editing session store — the Postgres editing buffer.

The DB is the source of truth for an in-progress *live* edit. There is **one
active session per page** (path-keyed); multiple humans join it and converge on
a single server-authoritative ``buffer_text`` + monotonic ``version``. A
single-user edit is just a 1-participant session — the model the per-user draft
will eventually fold into.

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
from datetime import datetime, timezone

from pydantic import BaseModel
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

from app.db.models import CoeditParticipant, CoeditSession, User
from app.db.session import session

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


class SessionRow(BaseModel):
    """A row from `coedit_sessions`."""

    id: int
    path: str
    buffer_text: str
    version: int
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


def _session_row(s: CoeditSession) -> SessionRow:
    return SessionRow(
        id=s.id,
        path=s.path,
        buffer_text=s.buffer_text,
        version=s.version,
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
    )


# --------------------------------------------------------------------------- #
# Sessions                                                                    #
# --------------------------------------------------------------------------- #


def get_active_session(path: str) -> SessionRow | None:
    """The active session for ``path``, or None if nobody is co-editing it."""
    with session() as s:
        row = s.scalar(
            select(CoeditSession).where(
                CoeditSession.path == path, CoeditSession.status == "active"
            )
        )
        return _session_row(row) if row is not None else None


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
                CoeditSession.path == path, CoeditSession.status == "active"
            )
        )
        if existing is not None:
            return _session_row(existing)
        fresh = CoeditSession(
            path=path,
            buffer_text=initial_buffer,
            version=0,
            base_sha=base_sha,
            status="active",
        )
        s.add(fresh)
        try:
            s.flush()
        except IntegrityError:
            # Another opener won the unique-index race — adopt their session.
            s.rollback()
            winner = s.scalar(
                select(CoeditSession).where(
                    CoeditSession.path == path, CoeditSession.status == "active"
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
                CoeditSession.status == "active",
            )
            .values(buffer_text=buffer_text, version=base_version + 1, updated_at=now)
            .returning(CoeditSession)
            .execution_options(synchronize_session=False)
        ).one_or_none()
        return _session_row(sess) if sess is not None else None


def mark_checkpointed(session_id: int, *, base_sha: str) -> None:
    """Record that the buffer was committed to git at ``base_sha``."""
    with session() as s:
        sess = s.get(CoeditSession, session_id)
        if sess is not None:
            sess.base_sha = base_sha
            sess.last_checkpoint_at = _iso(_now())


def close_session(session_id: int) -> None:
    """Mark a session closed, freeing the path for a new active session."""
    with session() as s:
        sess = s.get(CoeditSession, session_id)
        if sess is not None and sess.status != "closed":
            sess.status = "closed"
            sess.updated_at = _iso(_now())


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


def touch(session_id: int, user_id: str) -> None:
    """Refresh a participant's ``last_seen_at`` (presence heartbeat)."""
    with session() as s:
        existing = s.get(CoeditParticipant, (session_id, user_id))
        if existing is not None:
            existing.last_seen_at = _iso(_now())


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
