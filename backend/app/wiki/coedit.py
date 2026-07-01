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
from typing import Any

from pydantic import BaseModel
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

from app.db.models import CoeditOp, CoeditParticipant, CoeditSession, User
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


class OpRow(BaseModel):
    """One logged edit op from `coedit_ops`."""

    seq: int  # the session version this op produced
    author_user_id: str
    base_version: int
    changes: list[dict[str, Any]]
    created_at: str


def _apply_changes(text: str, changes: list[dict[str, Any]]) -> str:
    """Apply range-replacement ``changes`` to ``text``.

    Each change is ``{"from": int, "to": int, "insert": str}`` — replace the
    half-open range ``[from, to)`` with ``insert``. Offsets are relative to the
    *original* ``text`` (as the client saw it), and changes are expected
    non-overlapping; we apply them right-to-left (highest ``from`` first) so an
    earlier change's length delta never shifts a later change's offsets.

    **Offsets are UTF-16 code-unit indices**, matching JS / CodeMirror string
    positions (where an astral char like an emoji counts as 2). We therefore
    slice in UTF-16 space, *not* Python code points — otherwise any document
    containing an emoji or other non-BMP character would slice at the wrong
    place. Raises ``ValueError`` on an out-of-bounds range, overlapping ranges,
    or a range that splits a surrogate pair.
    """
    # 2 bytes per UTF-16 code unit → unit offset N is byte offset 2N.
    buf = bytearray(text.encode("utf-16-le"))
    n_units = len(buf) // 2

    # Parse first, so a malformed change surfaces as ValueError (not KeyError /
    # TypeError) — the whole function's error contract is ValueError.
    parsed: list[tuple[int, int, str]] = []
    for ch in changes:
        if "from" not in ch or "to" not in ch:
            raise ValueError(f"change missing 'from'/'to': {ch!r}")
        try:
            frm, to = int(ch["from"]), int(ch["to"])
        except (TypeError, ValueError) as e:
            raise ValueError(f"change has non-integer 'from'/'to': {ch!r}") from e
        parsed.append((frm, to, str(ch.get("insert", ""))))

    # Validate up front — bounds + non-overlap — rather than trust the caller's
    # unenforced "non-overlapping" contract. Overlapping ranges would apply onto
    # an already-mutated buffer and silently corrupt it.
    parsed.sort(key=lambda t: (t[0], t[1]))
    prev_to = 0
    for frm, to, _ in parsed:
        if not (0 <= frm <= to <= n_units):
            raise ValueError(f"change range [{frm},{to}) out of bounds for length {n_units}")
        if frm < prev_to:
            raise ValueError(f"overlapping change: [{frm},{to}) intrudes on a prior range ending at {prev_to}")
        prev_to = to

    # Apply right-to-left so an earlier change's length delta never shifts a
    # later change's (original-text) offsets.
    for frm, to, insert in reversed(parsed):
        buf[2 * frm : 2 * to] = insert.encode("utf-16-le")
    try:
        return bytes(buf).decode("utf-16-le")
    except UnicodeDecodeError as e:
        raise ValueError("change split a UTF-16 surrogate pair") from e


def apply_op(
    session_id: int,
    *,
    base_version: int,
    changes: list[dict[str, Any]],
    author_user_id: str,
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
                CoeditSession.status == "active",
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
                CoeditSession.status == "active",
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
                op_payload={"changes": changes},
            )
        )
        return _session_row(row)


def ops_since(session_id: int, after_version: int) -> list[OpRow]:
    """Logged ops with ``seq > after_version``, oldest first — for a late
    joiner (or a reconnecting client) to catch up incrementally."""
    with session() as s:
        rows = s.scalars(
            select(CoeditOp)
            .where(CoeditOp.session_id == session_id, CoeditOp.seq > after_version)
            .order_by(CoeditOp.seq.asc())
        ).all()
        return [
            OpRow(
                seq=o.seq,
                author_user_id=o.author_user_id,
                base_version=o.base_version,
                changes=list(o.op_payload.get("changes", [])),
                created_at=o.created_at,
            )
            for o in rows
        ]


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
