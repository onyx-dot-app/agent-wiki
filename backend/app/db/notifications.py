"""Repo for ``notifications`` — the persistent per-user notification center.

General subsystem (header bell / inbox); Craft launch outcomes are the
first producer. Create semantics follow Onyx's ``create_notification``:

* same (user, type, data) exists and is undismissed → bump ``last_shown``
* same key exists but was dismissed → leave it alone (don't resurrect)
* otherwise insert; a concurrent duplicate insert loses to the unique
  index and is retried as a bump.

``data`` is normalized to ``{}`` (the column is non-null) so the dedup
key is a plain column tuple.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError

from app.db.models import Notification
from app.db.session import execute_dml, session

log = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _to_dict(row: Notification) -> dict[str, Any]:
    return {
        "id": row.id,
        "user_id": row.user_id,
        "notif_type": row.notif_type,
        "title": row.title,
        "description": row.description,
        "dismissed": row.dismissed,
        "first_shown": row.first_shown,
        "last_shown": row.last_shown,
        "data": row.data,
    }


def _bump_if_undismissed(*, user_id: str, notif_type: str, data: dict[str, Any], now: str) -> bool:
    """Bump ``last_shown`` on an existing undismissed row. True if a row
    with this key exists at all (dismissed or not)."""
    with session() as s:
        row = s.scalar(
            select(Notification).where(
                Notification.user_id == user_id,
                Notification.notif_type == notif_type,
                Notification.data == data,
            )
        )
        if row is None:
            return False
        if not row.dismissed:
            row.last_shown = now
        return True


def create(
    *,
    user_id: str,
    notif_type: str,
    title: str,
    description: str | None = None,
    data: dict[str, Any] | None = None,
) -> None:
    """Idempotent create — see module docstring for the dedup semantics."""
    normalized: dict[str, Any] = data or {}
    now = _now_iso()
    if _bump_if_undismissed(user_id=user_id, notif_type=notif_type, data=normalized, now=now):
        return
    try:
        with session() as s:
            s.add(
                Notification(
                    user_id=user_id,
                    notif_type=notif_type,
                    title=title,
                    description=description,
                    first_shown=now,
                    last_shown=now,
                    data=normalized,
                )
            )
        log.info("notification created user=%s type=%s", user_id, notif_type)
    except IntegrityError:
        # Concurrent create with the same key won the insert — treat ours
        # as a re-notify of that row.
        _bump_if_undismissed(user_id=user_id, notif_type=notif_type, data=normalized, now=now)


def list_for_user(user_id: str, *, limit: int = 50, offset: int = 0) -> dict[str, Any]:
    """Newest-first page plus the badge counts the frontend needs."""
    with session() as s:
        rows = s.scalars(
            select(Notification)
            .where(Notification.user_id == user_id)
            .order_by(Notification.last_shown.desc(), Notification.id.desc())
            .limit(limit)
            .offset(offset)
        ).all()
        total = s.scalar(
            select(func.count()).select_from(Notification).where(Notification.user_id == user_id)
        )
        undismissed = s.scalar(
            select(func.count())
            .select_from(Notification)
            .where(Notification.user_id == user_id, Notification.dismissed.is_(False))
        )
        return {
            "notifications": [_to_dict(r) for r in rows],
            "total_items": int(total or 0),
            "undismissed_count": int(undismissed or 0),
            "has_more": offset + len(rows) < int(total or 0),
        }


def dismiss(notification_id: int, *, user_id: str) -> bool:
    """Mark one notification read. False when it isn't the caller's."""
    with session() as s:
        updated = execute_dml(
            s,
            update(Notification)
            .where(Notification.id == notification_id, Notification.user_id == user_id)
            .values(dismissed=True),
        )
    return bool(updated)


def dismiss_all(user_id: str) -> int:
    with session() as s:
        return execute_dml(
            s,
            update(Notification)
            .where(Notification.user_id == user_id, Notification.dismissed.is_(False))
            .values(dismissed=True),
        )


def delete_for_user(user_id: str) -> int:
    """Test/maintenance helper — bulk-remove a user's notifications."""
    with session() as s:
        return execute_dml(s, delete(Notification).where(Notification.user_id == user_id))
