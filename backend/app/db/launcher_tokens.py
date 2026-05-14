"""Encrypted plaintext bearer for launcher-minted MCP tokens.

The helper needs the raw bearer to wire claude/codex. Regular
``mcp_tokens`` only stores bcrypt hash, so the plaintext is lost at
creation. For launcher-minted tokens we keep an AES-GCM encrypted
plaintext here, decrypted server-side during ``POST /api/launch/exchange``.

Race fix: ``launcher_tokens.user_id`` is UNIQUE in the schema so
concurrent mints collide at the DB level. We try the optimistic
SELECT-then-INSERT; on conflict we revoke our orphan ``mcp_token`` and
return the winner's row.

Key rotation: if AES decrypt fails (operator rotated SECRET_KEY,
ciphertext corrupted), we log a warning, delete the stale row, and
re-mint. The launcher caller gets a fresh token instead of a 500.
"""

from __future__ import annotations

import hashlib
import logging
import secrets

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.auth import mcp_tokens as tokens_repo
from app.auth.passwords import hash_password
from app.config import CONFIG
from app.db.models import LauncherToken, McpToken
from app.db.session import execute_dml, session

log = logging.getLogger(__name__)


def _key() -> bytes:
    return hashlib.sha256(CONFIG.secret_key.encode("utf-8")).digest()


def _try_decrypt(row: LauncherToken) -> str | None:
    try:
        return AESGCM(_key()).decrypt(row.nonce, row.ciphertext, None).decode("utf-8")
    except Exception:
        log.warning(
            "launcher_token decrypt failed for user=%s (key rotation? corruption?); will re-mint",
            row.user_id,
        )
        return None


_TOKEN_BYTES = 24  # mirrors app.auth.mcp_tokens._TOKEN_BYTES


def _mint_plaintext() -> str:
    return tokens_repo.TOKEN_PREFIX + secrets.token_urlsafe(_TOKEN_BYTES)


def _remint_after_decrypt_failure(db: Session, row: LauncherToken) -> str:
    mcp_row = db.get(McpToken, row.mcp_token_id)
    if mcp_row is None:
        # Referential integrity should keep this aligned; guard defensively.
        log.error(
            "launcher_token remint failed: mcp_token row missing mcp_token_id=%s user_id=%s",
            row.mcp_token_id,
            row.user_id,
        )
        raise RuntimeError(
            f"launcher_token remint failed: missing mcp_token row {row.mcp_token_id}"
        )
    raw = _mint_plaintext()
    mcp_row.token_hash = hash_password(raw)
    nonce = secrets.token_bytes(12)
    row.nonce = nonce
    row.ciphertext = AESGCM(_key()).encrypt(nonce, raw.encode("utf-8"), None)
    log.info("launcher_token reminted after decrypt failure user=%s", row.user_id)
    return raw


def get_or_mint_for_user(user_id: str, *, name: str) -> tuple[str, str]:
    """Returns ``(token_id, raw_token)``. Re-uses an existing launcher
    token row if present and decryptable; otherwise mints a fresh one.
    Guarded against concurrent mint races by the UNIQUE constraint on
    ``launcher_tokens.user_id``.
    """
    # Optimistic SELECT.
    with session() as s:
        row = s.scalar(select(LauncherToken).where(LauncherToken.user_id == user_id))
        if row is not None:
            raw = _try_decrypt(row)
            if raw is not None:
                return row.mcp_token_id, raw
            # Stale ciphertext — drop the row, fall through to mint.
            s.delete(row)

    # Mint fresh.
    token_id, raw = tokens_repo.create(user_id, name)
    nonce = secrets.token_bytes(12)
    ciphertext = AESGCM(_key()).encrypt(nonce, raw.encode("utf-8"), None)
    with session() as s:
        stmt = (
            pg_insert(LauncherToken)
            .values(
                mcp_token_id=token_id,
                user_id=user_id,
                ciphertext=ciphertext,
                nonce=nonce,
            )
            .on_conflict_do_nothing(index_elements=["user_id"])
        )
        if execute_dml(s, stmt) == 0:
            # Lost the race. Revoke our orphan mcp_token, return the winner's.
            log.info("launcher_token mint race lost for user=%s; revoking orphan", user_id)
            tokens_repo.revoke(token_id, user_id)
            row = s.scalar(select(LauncherToken).where(LauncherToken.user_id == user_id))
            assert row is not None, "lost mint race but no winner row found"
            winner_raw = _try_decrypt(row)
            if winner_raw is None:
                raise RuntimeError("launcher_token race winner has undecryptable ciphertext")
            return row.mcp_token_id, winner_raw
    return token_id, raw


def get_raw_for_token_id(mcp_token_id: str) -> str | None:
    with session() as s:
        row = s.scalar(
            select(LauncherToken)
            .where(LauncherToken.mcp_token_id == mcp_token_id)
            .with_for_update()
        )
        if row is None:
            return None
        raw = _try_decrypt(row)
        if raw is not None:
            return raw
        return _remint_after_decrypt_failure(s, row)
