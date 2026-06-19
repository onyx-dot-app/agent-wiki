"""Repo for ``user_onyx_connections`` — one encrypted Onyx PAT per user.

The ``onyx_pat`` column is an ``EncryptedString`` so reads/writes are
plaintext here while the DB stores AES-GCM ciphertext. A decrypt failure
(SECRET_KEY rotation) is treated as "not connected": the stale row is
dropped and the user re-connects — mirrors ``launcher_tokens``.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from cryptography.exceptions import InvalidTag
from sqlalchemy import delete, select

from app.db.models import UserOnyxConnection
from app.db.session import execute_dml, session

log = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def mask_token(raw: str) -> str:
    """Display form: first 12 + last 4 chars (Onyx's PAT masking shape)."""
    if len(raw) <= 16:
        return raw[:4] + "…"
    return raw[:12] + "…" + raw[-4:]


def upsert(
    *,
    user_id: str,
    onyx_pat: str,
    onyx_user_email: str | None,
    expires_at: str | None,
    onyx_base_url: str,
) -> None:
    now = _now_iso()
    with session() as s:
        row = s.get(UserOnyxConnection, user_id)
        if row is None:
            s.add(
                UserOnyxConnection(
                    user_id=user_id,
                    onyx_pat=onyx_pat,
                    token_display=mask_token(onyx_pat),
                    onyx_user_email=onyx_user_email,
                    expires_at=expires_at,
                    onyx_base_url=onyx_base_url,
                    created_at=now,
                    updated_at=now,
                )
            )
        else:
            row.onyx_pat = onyx_pat
            row.token_display = mask_token(onyx_pat)
            row.onyx_user_email = onyx_user_email
            row.expires_at = expires_at
            row.onyx_base_url = onyx_base_url
            row.updated_at = now
    log.info("onyx connection upserted user=%s email=%s", user_id, onyx_user_email)


def get_with_pat(user_id: str, *, onyx_base_url: str) -> dict[str, Any] | None:
    """The full row incl. decrypted PAT, or None when not (validly) connected.

    None when: no row, the row was minted against a different Onyx origin
    than the current admin config, the PAT is past ``expires_at``, or the
    ciphertext no longer decrypts (key rotation — row is dropped).
    """
    try:
        with session() as s:
            row = s.get(UserOnyxConnection, user_id)
            if row is None:
                return None
            if row.onyx_base_url != onyx_base_url:
                return None
            if row.expires_at is not None and row.expires_at <= _now_iso():
                log.info("onyx connection expired user=%s; dropping row", user_id)
                s.delete(row)
                return None
            return {
                "user_id": row.user_id,
                "onyx_pat": row.onyx_pat,  # decrypted by EncryptedString
                "token_display": row.token_display,
                "onyx_user_email": row.onyx_user_email,
                "expires_at": row.expires_at,
                "onyx_base_url": row.onyx_base_url,
            }
    except InvalidTag:
        log.warning(
            "onyx connection decrypt failed user=%s (key rotation?); dropping row",
            user_id,
        )
        remove(user_id)
        return None


def status(user_id: str) -> dict[str, Any] | None:
    """Display-safe connection status (no PAT). None when no row exists."""
    with session() as s:
        row = (
            s.execute(
                select(
                    UserOnyxConnection.token_display,
                    UserOnyxConnection.onyx_user_email,
                    UserOnyxConnection.expires_at,
                    UserOnyxConnection.onyx_base_url,
                ).where(UserOnyxConnection.user_id == user_id)
            )
            .mappings()
            .first()
        )
    if row is None:
        return None
    data = dict(row)
    expires_at = data.get("expires_at")
    if expires_at is not None and expires_at <= _now_iso():
        log.info("onyx connection expired user=%s; dropping row", user_id)
        remove(user_id)
        return None
    return data


def remove(user_id: str) -> bool:
    with session() as s:
        deleted = execute_dml(
            s, delete(UserOnyxConnection).where(UserOnyxConnection.user_id == user_id)
        )
    if deleted:
        log.info("onyx connection removed user=%s", user_id)
    return bool(deleted)
