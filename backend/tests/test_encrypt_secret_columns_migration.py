"""0030 data migration: existing plaintext secret columns are encrypted in place.

Normal test schemas get the secret columns as ``bytea`` straight from
``0001_initial``'s ``create_all``, so the text->bytea conversion branch never
runs there. This test reproduces a pre-0030 database — text columns holding
plaintext — by rewinding ``llm_settings`` and the alembic stamp, then runs the
real ``init_db()`` (``alembic upgrade head``) so ``0030`` actually converts the
data.
"""
from __future__ import annotations

import sqlalchemy as sa

from app.db.crypto import decrypt_string
from app.db.session import init_db, session
from app.llm import settings as llm_settings

_PLAINTEXT = "sk-ant-PLAINTEXTKEY-0123456789abcdef"


def _rewind_to_text_with_plaintext() -> None:
    """Drop the encrypted columns, re-add them as text, seed plaintext, and
    move the alembic stamp back to the pre-0030 revision."""
    with session() as s:
        for col in (
            "anthropic_api_key",
            "openai_api_key",
            "gemini_api_key",
            "custom_api_key",
        ):
            s.execute(sa.text(f"ALTER TABLE llm_settings DROP COLUMN {col}"))
            s.execute(
                sa.text(
                    f"ALTER TABLE llm_settings ADD COLUMN {col} text NOT NULL DEFAULT ''"
                )
            )
        s.execute(
            sa.text("UPDATE llm_settings SET anthropic_api_key = :v WHERE id = 1"),
            {"v": _PLAINTEXT},
        )
        s.execute(sa.text("UPDATE alembic_version SET version_num = '4322ff468239'"))


def test_existing_plaintext_is_encrypted_in_place(tmp_db: object) -> None:
    # Seed a configured row (encrypted columns), then rewind to the pre-0030
    # plaintext-text shape.
    llm_settings.upsert(
        provider="anthropic",
        model="claude-opus-4-8",
        anthropic_api_key="placeholder",
        openai_api_key="",
        gemini_api_key="",
        ollama_base_url="",
        custom_api_key="",
        custom_base_url="",
        custom_display_name="",
    )
    _rewind_to_text_with_plaintext()

    # Re-run the bootstrapper — only 0030 is pending now.
    init_db()

    # Stored bytes are ciphertext, not the plaintext, and decrypt back to it.
    with session() as s:
        raw = s.execute(
            sa.text("SELECT anthropic_api_key FROM llm_settings WHERE id = 1")
        ).scalar_one()
    raw_bytes = bytes(raw)
    assert raw_bytes != _PLAINTEXT.encode()
    assert len(raw_bytes) > len(_PLAINTEXT)  # nonce + GCM tag overhead
    assert decrypt_string(raw_bytes) == _PLAINTEXT

    # And the repo reads the original value back transparently.
    assert llm_settings.get().anthropic_api_key == _PLAINTEXT
