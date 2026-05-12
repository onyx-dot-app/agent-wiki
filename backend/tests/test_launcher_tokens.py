"""Encrypted launcher-token storage + AF#3 race / AF#15 rotation."""

from __future__ import annotations

import secrets

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import select

from app.db.models import LauncherToken
from app.db.session import init_db, session
from app.launchers import launcher_tokens

from tests._seed import seed_user


def test_mint_returns_token_and_persists_plaintext(tmp_config):
    init_db()
    uid = seed_user()
    token_id, raw = launcher_tokens.get_or_mint_for_user(uid, name="claude-launcher")
    assert raw.startswith("mcp_")
    fetched = launcher_tokens.get_raw_for_token_id(token_id)
    assert fetched == raw


def test_second_call_returns_same_token(tmp_config):
    init_db()
    uid = seed_user()
    tid1, raw1 = launcher_tokens.get_or_mint_for_user(uid, name="launcher")
    tid2, raw2 = launcher_tokens.get_or_mint_for_user(uid, name="launcher")
    assert tid1 == tid2
    assert raw1 == raw2


def test_get_raw_unknown_id_returns_none(tmp_config):
    init_db()
    assert launcher_tokens.get_raw_for_token_id("mtk_nonexistent") is None


def test_decrypt_failure_remints_AF15(tmp_config):
    """AF#15 — corrupted ciphertext → re-mint instead of 500."""
    init_db()
    uid = seed_user()
    tid1, raw1 = launcher_tokens.get_or_mint_for_user(uid, name="launcher")
    # Corrupt the ciphertext on disk.
    with session() as s:
        row = s.scalar(select(LauncherToken).where(LauncherToken.user_id == uid))
        assert row is not None
        row.ciphertext = b"\x00" * len(row.ciphertext)
    # Next call should re-mint.
    tid2, raw2 = launcher_tokens.get_or_mint_for_user(uid, name="launcher")
    assert tid2 != tid1
    assert raw2 != raw1


def test_unique_user_id_constraint(tmp_config):
    """AF#3 — only one launcher_tokens row per user."""
    init_db()
    uid = seed_user()
    launcher_tokens.get_or_mint_for_user(uid, name="launcher")
    with session() as s:
        rows = s.scalars(select(LauncherToken).where(LauncherToken.user_id == uid)).all()
    assert len(rows) == 1
