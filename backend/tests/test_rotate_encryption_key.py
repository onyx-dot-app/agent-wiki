"""rotate_encryption_key: re-encrypt secret columns from an old key to the new one."""
from __future__ import annotations

import pytest
from cryptography.exceptions import InvalidTag
from sqlalchemy import Column, Integer, LargeBinary, MetaData, Table, select

from app.db import crypto
from app.db.rotate_encryption_key import rotate
from app.db.session import session
from app.llm import settings as llm_settings


def _raw_anthropic_key() -> bytes:
    """Raw ciphertext bytes of llm_settings.anthropic_api_key (bypasses EncryptedString)."""
    t = Table(
        "llm_settings",
        MetaData(),
        Column("id", Integer, primary_key=True),
        Column("anthropic_api_key", LargeBinary),
    )
    with session() as s:
        return bytes(s.execute(select(t.c.anthropic_api_key).where(t.c.id == 1)).scalar_one())


def test_rotate_reencrypts_under_new_key(tmp_db: object, monkeypatch: pytest.MonkeyPatch) -> None:
    llm_settings.upsert(
        provider="anthropic",
        model="m",
        anthropic_api_key="sk-rotate-me",
        openai_api_key="",
        gemini_api_key="",
        ollama_base_url="",
        custom_api_key="",
        custom_base_url="",
        custom_display_name="",
    )

    old_secret = crypto.active_key_secret()
    before = _raw_anthropic_key()
    assert crypto.decrypt_string(before, secret=old_secret) == "sk-rotate-me"

    # Point the active key at a new secret, then rotate from the old one.
    new_secret = old_secret + "-rotated-v2"
    monkeypatch.setattr(
        "app.db.crypto.CONFIG",
        crypto.CONFIG.model_copy(update={"encryption_key_secret": new_secret}),
    )
    touched = rotate(old_secret=old_secret)

    assert touched == 1  # the single llm_settings row
    after = _raw_anthropic_key()
    assert after != before  # genuinely re-encrypted
    # Reads now succeed under the new active key...
    assert llm_settings.get().anthropic_api_key == "sk-rotate-me"
    assert crypto.decrypt_string(after, secret=new_secret) == "sk-rotate-me"
    # ...and the old key no longer decrypts the rotated value.
    with pytest.raises(InvalidTag):
        crypto.decrypt_string(after, secret=old_secret)
