"""At-rest encryption for secret columns.

Follows the in-repo convention established by ``app/db/launcher_tokens.py``:
AES-256-GCM with a key derived from ``CONFIG.secret_key`` (the ``SECRET_KEY``
env var). Combined with the transparent ``TypeDecorator`` ergonomics so a
column just declares ``EncryptedString()`` and never sees ciphertext —
encrypt on write, decrypt on read.

Storage layout: a single ``bytea`` column holding ``nonce (12 bytes) ||
ciphertext`` per value. A fresh random nonce is generated on every write.

Key rotation caveat: values are tied to ``SECRET_KEY``. Rotating it makes
existing ciphertext undecryptable — a read raises ``cryptography``'s
``InvalidTag`` rather than silently returning garbage. Callers that can
recover (e.g. re-mint) should catch it; for user-supplied secrets the user
re-enters the value. (``launcher_tokens`` re-mints; it can, a webhook can't.)
"""
from __future__ import annotations

import hashlib
import secrets

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import LargeBinary
from sqlalchemy.engine.interfaces import Dialect
from sqlalchemy.types import TypeDecorator

from app.config import CONFIG

_NONCE_BYTES = 12  # AES-GCM standard nonce length


def _key() -> bytes:
    """32-byte AES key derived from SECRET_KEY (mirrors launcher_tokens)."""
    return hashlib.sha256(CONFIG.secret_key.encode("utf-8")).digest()


def encrypt_string(plaintext: str) -> bytes:
    """Return ``nonce || ciphertext`` for ``plaintext`` under AES-256-GCM."""
    nonce = secrets.token_bytes(_NONCE_BYTES)
    ciphertext = AESGCM(_key()).encrypt(nonce, plaintext.encode("utf-8"), None)
    return nonce + ciphertext


def decrypt_string(blob: bytes) -> str:
    """Inverse of :func:`encrypt_string`. Raises ``InvalidTag`` on a wrong key."""
    nonce, ciphertext = blob[:_NONCE_BYTES], blob[_NONCE_BYTES:]
    return AESGCM(_key()).decrypt(nonce, ciphertext, None).decode("utf-8")


class EncryptedString(TypeDecorator[str]):
    """A ``str`` column transparently AES-GCM encrypted at rest (``bytea``).

    Declare with ``mapped_column(EncryptedString())``; reads/writes are plain
    ``str``. ``cache_ok`` is safe — the type carries no per-instance config.
    """

    impl = LargeBinary
    cache_ok = True

    def process_bind_param(self, value: str | None, dialect: Dialect) -> bytes | None:
        if value is None:
            return None
        return encrypt_string(value)

    def process_result_value(self, value: bytes | None, dialect: Dialect) -> str | None:
        if value is None:
            return None
        return decrypt_string(bytes(value))
