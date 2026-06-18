"""Re-encrypt every ``EncryptedString`` column from an old key to the current one.

Run during a maintenance window after pointing ``ENCRYPTION_KEY_SECRET`` at a new
value (and before the app reads any of these columns under the new key)::

    OLD_ENCRYPTION_KEY_SECRET=<previous-secret> python -m app.scripts.rotate_encryption_key

For each secret column it reads the raw ``nonce || ciphertext`` bytes — via a
Core table typed as ``LargeBinary`` so the ``EncryptedString`` decorator's
auto-encrypt/decrypt is bypassed — decrypts with the old key, re-encrypts with
the current key, and writes the bytes back. NULLs are skipped.

``launcher_tokens`` are intentionally not rotated: they self-heal by re-minting
when their ciphertext fails to decrypt (see ``app/db/launcher_tokens.py``).

The "old" secret is whatever the active key was before the change — i.e. the
previous ``ENCRYPTION_KEY_SECRET``, or ``SECRET_KEY`` if a dedicated encryption
key is being introduced for the first time.
"""
from __future__ import annotations

import logging
import os
from typing import Any

from sqlalchemy import Column, Integer, LargeBinary, MetaData, Table, Text, select, update

from app.db.crypto import active_key_secret, decrypt_string, encrypt_string
from app.db.session import session

log = logging.getLogger(__name__)

# (table, primary-key column, primary-key type, [encrypted columns]).
# Keep in sync with the EncryptedString columns in app/db/models.py.
_TARGETS: list[tuple[str, str, type, list[str]]] = [
    ("slack_webhooks", "id", Text, ["webhook_url"]),
    (
        "llm_settings",
        "id",
        Integer,
        ["anthropic_api_key", "openai_api_key", "gemini_api_key", "custom_api_key"],
    ),
    ("web_settings", "id", Integer, ["serper_api_key", "firecrawl_api_key"]),
    ("ingest_settings", "id", Integer, ["api_key"]),
    ("braintrust_settings", "id", Integer, ["api_key"]),
]


def rotate(old_secret: str, new_secret: str | None = None) -> int:
    """Re-encrypt all secret columns from ``old_secret`` to ``new_secret``.

    ``new_secret`` defaults to the currently-active key. Returns the number of
    rows touched. Raises ``cryptography``'s ``InvalidTag`` if ``old_secret`` is
    wrong for a value — nothing in that table is committed (the session rolls
    back), so a bad key fails loudly instead of corrupting data.
    """
    new = new_secret if new_secret is not None else active_key_secret()
    rows_touched = 0
    for table_name, pk, pk_type, cols in _TARGETS:
        columns: list[Column[Any]] = [Column(pk, pk_type, primary_key=True)]
        columns += [Column(c, LargeBinary) for c in cols]
        table = Table(table_name, MetaData(), *columns)
        with session() as s:
            for row in s.execute(select(table)).mappings().all():
                changes = {
                    c: encrypt_string(decrypt_string(bytes(row[c]), secret=old_secret), secret=new)
                    for c in cols
                    if row[c] is not None
                }
                if changes:
                    s.execute(update(table).where(table.c[pk] == row[pk]).values(**changes))
                    rows_touched += 1
    log.info(
        "rotate_encryption_key: re-encrypted %d rows across %d tables",
        rows_touched,
        len(_TARGETS),
    )
    return rows_touched


def main() -> None:
    from app.utils.logging import setup_logging

    setup_logging()
    old = os.environ.get("OLD_ENCRYPTION_KEY_SECRET")
    if not old:
        raise SystemExit(
            "OLD_ENCRYPTION_KEY_SECRET must be set to the previous encryption key "
            "(the prior ENCRYPTION_KEY_SECRET, or SECRET_KEY if introducing a "
            "dedicated key for the first time)."
        )
    rotate(old)


if __name__ == "__main__":
    main()
