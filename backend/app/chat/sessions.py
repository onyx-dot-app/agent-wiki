"""Chat session repo — SQLAlchemy ORM. Free functions over the
``ChatSession`` and ``ChatMessage`` models.

Session ownership is enforced here: ``get`` and ``delete`` filter by
``user_id`` so the API layer can't accidentally return another user's
conversation. Use ``append_message`` for both user and assistant turns;
it allocates the next ``ordering`` value for the session.
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select

from app.db.models import ChatMessage, ChatSession
from app.db.session import session

log = logging.getLogger(__name__)


def _session_to_dict(s: ChatSession) -> dict[str, Any]:
    return {
        "id": s.id,
        "user_id": s.user_id,
        "title": s.title,
        "hidden": s.hidden,
        "created_at": s.created_at,
        "updated_at": s.updated_at,
    }


def _message_to_dict(m: ChatMessage) -> dict[str, Any]:
    events: list[dict[str, Any]] | None = None
    if m.events_json:
        try:
            events = json.loads(m.events_json)
        except (TypeError, ValueError):
            log.warning("chat_messages id=%s has malformed events_json", m.id)
            events = None
    return {
        "id": m.id,
        "session_id": m.session_id,
        "ordering": m.ordering,
        "role": m.role,
        "content": m.content,
        "events": events,
        "hidden": m.hidden,
        "created_at": m.created_at,
    }


def create(user_id: str, *, hidden: bool = False) -> dict[str, Any]:
    sid = str(uuid.uuid4())
    with session() as s:
        row = ChatSession(id=sid, user_id=user_id, title=None, hidden=hidden)
        s.add(row)
        s.flush()
        s.refresh(row)
        return _session_to_dict(row)


def get(session_id: str, user_id: str) -> dict[str, Any] | None:
    with session() as s:
        row = s.scalar(
            select(ChatSession).where(
                ChatSession.id == session_id,
                ChatSession.user_id == user_id,
            )
        )
        return _session_to_dict(row) if row else None


def list_for_user(user_id: str) -> list[dict[str, Any]]:
    """User-visible sessions: hidden=TRUE rows are excluded by design."""
    with session() as s:
        rows = s.scalars(
            select(ChatSession)
            .where(
                ChatSession.user_id == user_id,
                ChatSession.hidden.is_(False),
            )
            .order_by(ChatSession.updated_at.desc())
        ).all()
        return [_session_to_dict(r) for r in rows]


def delete(session_id: str, user_id: str) -> bool:
    """Hard-delete the session (and its messages, via FK CASCADE).

    Returns True if a row was deleted, False if it didn't exist or
    wasn't owned by ``user_id``.
    """
    with session() as s:
        row = s.scalar(
            select(ChatSession).where(
                ChatSession.id == session_id,
                ChatSession.user_id == user_id,
            )
        )
        if row is None:
            return False
        s.delete(row)
        return True


def update_title(session_id: str, title: str) -> None:
    if title:
        title = title[:1].upper() + title[1:]
    with session() as s:
        row = s.get(ChatSession, session_id)
        if row is not None:
            row.title = title


def touch(session_id: str) -> None:
    """Bump ``updated_at`` so the session sorts to the top of the list."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    with session() as s:
        row = s.get(ChatSession, session_id)
        if row is not None:
            row.updated_at = now


def append_message(
    session_id: str,
    *,
    role: str,
    content: str,
    events: list[dict[str, Any]] | None = None,
    hidden: bool = False,
) -> dict[str, Any]:
    """Append a message and return the inserted row as a dict.

    Allocates ``ordering`` as ``max(ordering)+1`` for the session.

    ``hidden=True`` keeps the row out of UI transcripts (``get_messages``
    defaults to filtering it) but it still flows into the LLM history
    when callers request ``include_hidden=True``.
    """
    if role not in ("user", "assistant"):
        raise ValueError(f"invalid role: {role!r}")
    mid = str(uuid.uuid4())
    events_json = json.dumps(events) if events is not None else None
    with session() as s:
        # COALESCE(..., -1) makes the empty-table case return -1, then +1 → 0.
        # Don't fold the COALESCE into a Python ``or`` — the second row's
        # max is 0, which Python treats as falsy and would resolve back to -1.
        max_order = s.scalar(
            select(func.coalesce(func.max(ChatMessage.ordering), -1)).where(
                ChatMessage.session_id == session_id
            )
        )
        next_order = (max_order if max_order is not None else -1) + 1
        row = ChatMessage(
            id=mid,
            session_id=session_id,
            ordering=next_order,
            role=role,
            content=content,
            events_json=events_json,
            hidden=hidden,
        )
        s.add(row)
        s.flush()
        s.refresh(row)
        return _message_to_dict(row)


def get_messages(
    session_id: str, *, include_hidden: bool = False,
) -> list[dict[str, Any]]:
    """Return messages in order. ``include_hidden=False`` (default) drops
    rows the UI shouldn't render — pass ``True`` when rebuilding LLM
    history so the model still sees seed turns the user never wrote."""
    with session() as s:
        stmt = (
            select(ChatMessage)
            .where(ChatMessage.session_id == session_id)
            .order_by(ChatMessage.ordering.asc())
        )
        if not include_hidden:
            stmt = stmt.where(ChatMessage.hidden.is_(False))
        rows = s.scalars(stmt).all()
        return [_message_to_dict(r) for r in rows]


def count_messages(session_id: str) -> int:
    with session() as s:
        return (
            s.scalar(
                select(func.count())
                .select_from(ChatMessage)
                .where(ChatMessage.session_id == session_id)
            )
            or 0
        )
