"""Key derivation for at-rest encryption: dedicated secret with SECRET_KEY fallback."""
from __future__ import annotations

import hashlib

import pytest
from cryptography.exceptions import InvalidTag

from app.db import crypto


def test_round_trip_with_explicit_secret() -> None:
    blob = crypto.encrypt_string("hunter2", secret="key-a")
    assert crypto.decrypt_string(blob, secret="key-a") == "hunter2"


def test_wrong_secret_raises_rather_than_returning_garbage() -> None:
    blob = crypto.encrypt_string("hunter2", secret="key-a")
    with pytest.raises(InvalidTag):
        crypto.decrypt_string(blob, secret="key-b")


def test_key_is_sha256_of_secret() -> None:
    assert crypto._key("key-a") == hashlib.sha256(b"key-a").digest()


def test_active_secret_falls_back_to_secret_key_when_encryption_key_unset(monkeypatch) -> None:
    cfg = crypto.CONFIG.model_copy(update={"encryption_key_secret": "", "secret_key": "sk"})
    monkeypatch.setattr("app.db.crypto.CONFIG", cfg)
    assert crypto.active_key_secret() == "sk"


def test_active_secret_prefers_encryption_key_when_set(monkeypatch) -> None:
    cfg = crypto.CONFIG.model_copy(update={"encryption_key_secret": "ek", "secret_key": "sk"})
    monkeypatch.setattr("app.db.crypto.CONFIG", cfg)
    assert crypto.active_key_secret() == "ek"


def test_fallback_decrypts_secret_key_era_ciphertext(monkeypatch) -> None:
    # A value written before a dedicated key existed (encrypted under SECRET_KEY)
    # must still decrypt when ENCRYPTION_KEY_SECRET is unset.
    cfg = crypto.CONFIG.model_copy(update={"encryption_key_secret": "", "secret_key": "sk"})
    monkeypatch.setattr("app.db.crypto.CONFIG", cfg)
    blob = crypto.encrypt_string("legacy-value")
    assert crypto.decrypt_string(blob) == "legacy-value"
