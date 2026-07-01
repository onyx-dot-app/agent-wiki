"""Repo for ``user_slack_connections`` — one encrypted bot token per user per
workspace.

Follows ``app/onyx/connections.py``: reads/writes are plaintext here while the
DB stores AES-GCM ciphertext, and a decrypt failure (key rotation) is treated
as "not connected" — the stale row is dropped and the user re-connects.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from cryptography.exceptions import InvalidTag
from sqlalchemy import delete, select

from app.db.models import UserSlackConnection
from app.db.session import session
from app.onyx.connections import mask_token

log = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _to_dict(c: UserSlackConnection) -> dict[str, Any]:
    # Token deliberately omitted. Resolve it via ``get_bot_token``.
    return {
        "user_id": c.user_id,
        "team_id": c.team_id,
        "team_name": c.team_name,
        "slack_user_id": c.slack_user_id,
        "token_display": c.token_display,
        "scope": c.scope,
        "created_at": c.created_at,
        "updated_at": c.updated_at,
    }


def upsert(
    *,
    user_id: str,
    team_id: str,
    team_name: str | None,
    slack_user_id: str,
    bot_token: str,
    scope: str | None,
) -> None:
    now = _now_iso()
    with session() as s:
        row = s.get(UserSlackConnection, (user_id, team_id))
        if row is None:
            s.add(
                UserSlackConnection(
                    user_id=user_id,
                    team_id=team_id,
                    team_name=team_name,
                    slack_user_id=slack_user_id,
                    bot_token=bot_token,
                    token_display=mask_token(bot_token),
                    scope=scope,
                    created_at=now,
                    updated_at=now,
                )
            )
        else:
            row.team_name = team_name
            row.slack_user_id = slack_user_id
            row.bot_token = bot_token
            row.token_display = mask_token(bot_token)
            row.scope = scope
            row.updated_at = now
    log.info("slack connection upserted user=%s team=%s", user_id, team_id)


def list_for_user(user_id: str) -> list[dict[str, Any]]:
    with session() as s:
        rows = s.scalars(
            select(UserSlackConnection).where(UserSlackConnection.user_id == user_id)
        ).all()
        return [_to_dict(c) for c in rows]


def get(user_id: str, team_id: str) -> dict[str, Any] | None:
    with session() as s:
        row = s.get(UserSlackConnection, (user_id, team_id))
        return _to_dict(row) if row else None


def delete_connection(user_id: str, team_id: str) -> bool:
    with session() as s:
        row = s.get(UserSlackConnection, (user_id, team_id))
        if row is None:
            return False
        s.delete(row)
    log.info("slack connection deleted user=%s team=%s", user_id, team_id)
    return True


def get_bot_token(user_id: str, team_id: str) -> str | None:
    """Decrypt the bot token, owner-scoped. A decrypt failure (key rotation)
    drops the row and reads as not-connected."""
    try:
        with session() as s:
            row = s.get(UserSlackConnection, (user_id, team_id))
            return row.bot_token if row else None
    except InvalidTag:
        log.warning(
            "slack connection token undecryptable (key rotated?); dropping user=%s team=%s",
            user_id, team_id,
        )
        with session() as s:
            s.execute(
                delete(UserSlackConnection).where(
                    UserSlackConnection.user_id == user_id,
                    UserSlackConnection.team_id == team_id,
                )
            )
        return None
