"""Repo for ``launch_codes`` — short-lived single-use bearers the helper exchanges for the MCP token + manifest payload.

See ``local_data/wiki/Wiki Project/Specific Features/coding_tool_launchers/design.md``.

TTL is read from ``CONFIG.launch_code_ttl_seconds`` so ops can bump it
without a code change (Windows cold starts have been the motivating
case).
"""

from __future__ import annotations

import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from sqlalchemy import delete, select

from app.config import CONFIG
from app.db.models import LaunchCode
from app.db.session import execute_dml, session

log = logging.getLogger(__name__)

_TOKEN_PREFIX = "lc_"
_TOKEN_BYTES = 32


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def create(*, user_id: str, agent_session_id: str, mcp_token_id: str) -> str:
    """Mint a fresh launch code. Returns the raw value."""
    raw = _TOKEN_PREFIX + secrets.token_urlsafe(_TOKEN_BYTES)
    now = datetime.now(timezone.utc)
    expires_at = _iso(now + timedelta(seconds=CONFIG.launch_code_ttl_seconds))
    with session() as s:
        s.add(
            LaunchCode(
                id=raw,
                user_id=user_id,
                agent_session_id=agent_session_id,
                mcp_token_id=mcp_token_id,
                expires_at=expires_at,
            )
        )
    log.info(
        "launch_code minted user=%s session=%s expires=%s",
        user_id,
        agent_session_id,
        expires_at,
    )
    return raw


def consume(
    raw: str,
) -> dict[str, Any] | Literal["already_consumed", "expired"] | None:
    """Atomically claim a code.

    Returns:
        * ``dict`` with ``{user_id, agent_session_id, mcp_token_id}`` on success.
        * ``"already_consumed"`` if the code was already exchanged.
        * ``"expired"`` if past ``expires_at``.
        * ``None`` if the code doesn't exist or is malformed.
    """
    if not raw.startswith(_TOKEN_PREFIX):
        return None
    now_iso = _iso(datetime.now(timezone.utc))
    with session() as s:
        row = s.scalar(select(LaunchCode).where(LaunchCode.id == raw).with_for_update())
        if row is None:
            return None
        if row.consumed_at is not None:
            return "already_consumed"
        if row.expires_at <= now_iso:
            return "expired"
        row.consumed_at = now_iso
        return {
            "user_id": row.user_id,
            "agent_session_id": row.agent_session_id,
            "mcp_token_id": row.mcp_token_id,
        }


def expire_sweep() -> int:
    """Delete codes past ``expires_at``. Returns count deleted."""
    now_iso = _iso(datetime.now(timezone.utc))
    with session() as s:
        return execute_dml(s, delete(LaunchCode).where(LaunchCode.expires_at <= now_iso))
