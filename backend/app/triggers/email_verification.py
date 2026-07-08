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

from app.config import CONFIG
from app.db.models import DestinationConfig, EmailVerificationToken
from app.db.session import session
from app.email import service as email_service

log = logging.getLogger(__name__)

_TOKEN_PREFIX = "evt_"
# Email links get opened from an inbox, not a same-minute redirect — a day.
_TOKEN_TTL_SECONDS = 24 * 60 * 60
# Floor between verification sends per config; the send costs real mail.
_MINT_COOLDOWN_SECONDS = 60


class MintRateLimitedError(RuntimeError):
    """A verification email for this config was sent too recently."""

    def __init__(self, message: str, retry_after_seconds: int) -> None:
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def mint_token(destination_config_id: str) -> str:
    """Create a verification token for the config, clearing any prior one so
    a single token is outstanding per destination. Raises
    ``MintRateLimitedError`` inside the cooldown window."""
    token = _TOKEN_PREFIX + secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    floor = _iso(now - timedelta(seconds=_MINT_COOLDOWN_SECONDS))
    with session() as s:
        recent = s.scalar(
            select(EmailVerificationToken.created_at).where(
                EmailVerificationToken.destination_config_id == destination_config_id,
                EmailVerificationToken.created_at > floor,
                EmailVerificationToken.consumed_at.is_(None),
            )
        )
        if recent is not None:
            sent_at = datetime.strptime(recent, "%Y-%m-%d %H:%M:%S").replace(
                tzinfo=timezone.utc
            )
            elapsed = int((now - sent_at).total_seconds())
            retry_after = max(1, _MINT_COOLDOWN_SECONDS - elapsed)
            raise MintRateLimitedError(
                "a verification email was just sent; wait a minute", retry_after
            )
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


def verify(token: str) -> str | None:
    """Claim a token and stamp its config verified in one transaction.
    Returns the config id, or None on unknown/expired/replayed tokens.

    Deliberately unauthenticated: the link may land in an inbox that isn't
    the config owner's (adding someone else's address), and the click there
    is the consent. The single-use, expiring, unguessable token is the
    capability."""
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
        if config is None:
            return None
        row.consumed_at = now_iso
        config.verified_at = now_iso
        return config.id


def send_verification_email(destination_config_id: str, address: str) -> None:
    """Mint a token and mail its verify link to ``address``. Raises
    ``MintRateLimitedError`` inside the cooldown and ``EmailSendError`` when
    the send itself fails (token is cleared so a retry isn't rate-limited)."""
    token = mint_token(destination_config_id)
    link = f"{CONFIG.public_base_url}/api/email/verify?token={token}"
    try:
        email_service.send(
            to=address,
            subject="Verify this address for Agent Wiki notifications",
            text=(
                "Someone added this address as an Agent Wiki notification "
                "destination. Click to confirm you want to receive them:\n\n"
                f"{link}\n\nThe link expires in 24 hours. If this wasn't "
                "expected, ignore this email and nothing will be sent here."
            ),
        )
    except email_service.EmailSendError:
        with session() as s:
            s.execute(
                delete(EmailVerificationToken).where(EmailVerificationToken.token == token)
            )
        raise
    log.info("verification email sent config=%s", destination_config_id)
