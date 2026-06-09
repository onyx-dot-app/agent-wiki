"""Connect-Onyx handshake domain: single-use CSRF state + PKCE.

Flow (OAuth-shaped, on Onyx's PAT primitive — Onyx is not an OAuth server):

1. ``mint_state()`` — random single-use ``state`` row holding the PKCE
   ``code_verifier`` server-side (it never rides a browser-visible URL)
   plus an optional validated ``return_to``.
2. The browser is redirected to ``{onyx}/connect/agent-wiki`` with
   ``state`` + the S256 ``code_challenge``.
3. Onyx redirects back with a one-time ``code``; ``consume_state()``
   verifies single-use + TTL + same-user, then the backend exchanges
   ``code`` + ``code_verifier`` server-to-server for the raw PAT.
"""

from __future__ import annotations

import hashlib
import logging
import secrets
from base64 import urlsafe_b64encode
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlencode

from sqlalchemy import delete, select

from app.db.models import CraftConnectState
from app.db.session import session

log = logging.getLogger(__name__)

_STATE_TTL_SECONDS = 600
_STATE_PREFIX = "cs_"


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _now_iso() -> str:
    return _iso(datetime.now(timezone.utc))


def normalize_return_to(raw: str | None) -> str | None:
    """Only same-origin relative paths survive — no open redirects."""
    if not raw:
        return None
    if not raw.startswith("/") or raw.startswith("//") or "\\" in raw:
        return None
    return raw


def _code_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def mint_state(*, user_id: str, return_to: str | None) -> tuple[str, str]:
    """Create a state row; returns ``(state, code_challenge)``.

    Opportunistically clears this user's expired/stale states so abandoned
    handshakes don't accumulate.
    """
    state = _STATE_PREFIX + secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)  # 86 chars — within PKCE's 43-128
    now = datetime.now(timezone.utc)
    with session() as s:
        s.execute(delete(CraftConnectState).where(CraftConnectState.user_id == user_id))
        s.add(
            CraftConnectState(
                state=state,
                user_id=user_id,
                code_verifier=verifier,
                return_to=normalize_return_to(return_to),
                expires_at=_iso(now + timedelta(seconds=_STATE_TTL_SECONDS)),
            )
        )
    log.info("craft connect state minted user=%s", user_id)
    return state, _code_challenge(verifier)


def consume_state(state: str, *, user_id: str) -> dict[str, Any] | None:
    """Atomically claim a state row. None on unknown/expired/replayed/foreign."""
    if not state.startswith(_STATE_PREFIX):
        return None
    now_iso = _now_iso()
    with session() as s:
        row = s.scalar(
            select(CraftConnectState).where(CraftConnectState.state == state).with_for_update()
        )
        if row is None:
            return None
        if row.user_id != user_id:
            log.warning(
                "craft connect state user mismatch state_user=%s caller=%s", row.user_id, user_id
            )
            return None
        if row.consumed_at is not None or row.expires_at <= now_iso:
            return None
        row.consumed_at = now_iso
        return {"code_verifier": row.code_verifier, "return_to": row.return_to}


def build_authorize_url(
    onyx_base_url: str, *, redirect_uri: str, state: str, code_challenge: str
) -> str:
    query = urlencode(
        {
            "redirect_uri": redirect_uri,
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }
    )
    return f"{onyx_base_url}/connect/agent-wiki?{query}"
