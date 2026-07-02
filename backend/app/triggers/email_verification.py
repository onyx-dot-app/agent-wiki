"""Single-use verification tokens for email destination configs.

Mirrors the connect-state pattern: minted once per config (re-mint clears
prior tokens), consumed exactly once, TTL-bounded. Consuming a valid token
stamps the config's ``verified_at`` — unverified email destinations are
recorded-only at dispatch.
"""
from __future__ import annotations

import logging
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select

from app.db.models import DestinationConfig, EmailVerificationToken
from app.db.session import session

log = logging.getLogger(__name__)

_TOKEN_PREFIX = "evt_"
# Email links get opened from an inbox, not a same-minute redirect — a day.
_TOKEN_TTL_SECONDS = 24 * 60 * 60


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def mint_token(destination_config_id: str) -> str:
    """Create a verification token for the config, clearing any prior one so
    a single token is outstanding per destination."""
    token = _TOKEN_PREFIX + secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    with session() as s:
        s.execute(
            delete(EmailVerificationToken).where(
                EmailVerificationToken.destination_config_id == destination_config_id
            )
        )
        s.add(
            EmailVerificationToken(
                token=token,
                destination_config_id=destination_config_id,
                expires_at=_iso(now + timedelta(seconds=_TOKEN_TTL_SECONDS)),
            )
        )
    log.info("email verification token minted config=%s", destination_config_id)
    return token


def verify(token: str, *, owner_user_id: str) -> str | None:
    """Claim a token and stamp its config verified in one transaction.
    Returns the config id, or None on unknown/expired/replayed tokens. A
    foreign-owner attempt leaves the token unconsumed so the real owner can
    still verify."""
    if not token.startswith(_TOKEN_PREFIX):
        return None
    now_iso = _iso(datetime.now(timezone.utc))
    with session() as s:
        row = s.scalar(
            select(EmailVerificationToken)
            .where(EmailVerificationToken.token == token)
            .with_for_update()
        )
        if row is None or row.consumed_at is not None or row.expires_at <= now_iso:
            return None
        config = s.get(DestinationConfig, row.destination_config_id)
        if config is None or config.owner_user_id != owner_user_id:
            log.warning(
                "email verification rejected: config %s not owned by %s",
                row.destination_config_id, owner_user_id,
            )
            return None
        row.consumed_at = now_iso
        config.verified_at = now_iso
        return config.id
