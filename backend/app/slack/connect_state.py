"""Single-use CSRF state for the Connect-Slack handshake.

Mirrors ``app/onyx/connect.py``'s mint/consume pair minus PKCE: Slack's OAuth
v2 authenticates the code exchange with the client secret, so only the state
token needs minting and single-use consumption.
"""

from __future__ import annotations

import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import delete, select

from app.db.models import SlackConnectState
from app.db.session import session
from app.onyx.connect import normalize_return_to

log = logging.getLogger(__name__)

_STATE_PREFIX = "slkst_"
_STATE_TTL_SECONDS = 600


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _now_iso() -> str:
    return _iso(datetime.now(timezone.utc))


def mint_state(*, user_id: str, return_to: str | None) -> str:
    """Create a state row and return the state token.

    Clears any prior state rows for this user first — one connect handshake
    in flight per user, so abandoned ones don't accumulate.
    """
    state = _STATE_PREFIX + secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    with session() as s:
        s.execute(delete(SlackConnectState).where(SlackConnectState.user_id == user_id))
        s.add(
            SlackConnectState(
                state=state,
                user_id=user_id,
                return_to=normalize_return_to(return_to),
                expires_at=_iso(now + timedelta(seconds=_STATE_TTL_SECONDS)),
            )
        )
    log.info("slack connect state minted user=%s", user_id)
    return state


def consume_state(state: str, *, user_id: str) -> dict[str, Any] | None:
    """Atomically claim a state row. None on unknown/expired/replayed/foreign."""
    if not state.startswith(_STATE_PREFIX):
        return None
    now_iso = _now_iso()
    with session() as s:
        row = s.scalar(
            select(SlackConnectState).where(SlackConnectState.state == state).with_for_update()
        )
        if row is None:
            return None
        if row.user_id != user_id:
            log.warning(
                "slack connect state user mismatch state_user=%s caller=%s", row.user_id, user_id
            )
            return None
        if row.consumed_at is not None or row.expires_at <= now_iso:
            return None
        row.consumed_at = now_iso
        return {"return_to": row.return_to}
