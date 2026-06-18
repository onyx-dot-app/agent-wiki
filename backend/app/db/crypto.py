"""At-rest encryption for secret columns.

Follows the in-repo convention established by ``app/db/launcher_tokens.py``:
AES-256-GCM with a key derived from ``CONFIG.secret_key`` (the ``SECRET_KEY``
env var). Combined with the transparent ``TypeDecorator`` ergonomics so a
column just declares ``EncryptedString()`` and never sees ciphertext —
encrypt on write, decrypt on read.

Storage layout: a single ``bytea`` column holding ``nonce (12 bytes) ||
ciphertext`` per value. A fresh random nonce is generated on every write.

Key source: ``ENCRYPTION_KEY_SECRET`` when set, else ``SECRET_KEY`` (the
historical default). Splitting the encryption secret from the cookie-signing
key lets it rotate independently. Changing the active key makes existing
ciphertext undecryptable (a read raises ``cryptography``'s ``InvalidTag``
rather than returning garbage), so rotate it with
``app/scripts/rotate_encryption_key.py``, which re-encrypts every column from
the old key to the new one. ``launcher_tokens`` instead re-mints on a key change;
it can, a webhook can't.
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


def active_key_secret() -> str:
    """Secret material the AES key is derived from: ``ENCRYPTION_KEY_SECRET``
    when set, else ``SECRET_KEY``. The fallback keeps deployments that never
    set a dedicated encryption key (and their existing ciphertext) working."""
    return CONFIG.encryption_key_secret or CONFIG.secret_key


def _key(secret: str | None = None) -> bytes:
    """32-byte AES key derived from ``secret`` (defaults to the active secret).

    An explicit ``secret`` lets the rotation tool decrypt under the old key and
    re-encrypt under the new one in the same process."""
    material = secret if secret is not None else active_key_secret()
    return hashlib.sha256(material.encode("utf-8")).digest()


def encrypt_string(plaintext: str, *, secret: str | None = None) -> bytes:
    """Return ``nonce || ciphertext`` for ``plaintext`` under AES-256-GCM."""
    nonce = secrets.token_bytes(_NONCE_BYTES)
    ciphertext = AESGCM(_key(secret)).encrypt(nonce, plaintext.encode("utf-8"), None)
    return nonce + ciphertext


def decrypt_string(blob: bytes, *, secret: str | None = None) -> str:
    """Inverse of :func:`encrypt_string`. Raises ``InvalidTag`` on a wrong key."""
    nonce, ciphertext = blob[:_NONCE_BYTES], blob[_NONCE_BYTES:]
    return AESGCM(_key(secret)).decrypt(nonce, ciphertext, None).decode("utf-8")


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
