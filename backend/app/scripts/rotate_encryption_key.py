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

from sqlalchemy import Column, LargeBinary, MetaData, Table, and_, select, update

from app.db.crypto import EncryptedString, active_key_secret, decrypt_string, encrypt_string
from app.db.models import Base
from app.db.session import session

log = logging.getLogger(__name__)


def _encrypted_targets() -> list[tuple[Table, list[str], list[str]]]:
    """Discover every ``EncryptedString`` column from the ORM models.

    Returns, per table that has at least one, a Core ``Table`` whose secret
    columns are typed as raw ``LargeBinary`` (so the ``EncryptedString``
    decorator's auto-encrypt/decrypt is bypassed and we see the stored bytes),
    plus its primary-key and encrypted column names. Deriving this from the
    mapper rather than a hand-maintained list means a newly added
    ``EncryptedString`` column is rotated automatically, never silently skipped.
    """
    targets: list[tuple[Table, list[str], list[str]]] = []
    for table in Base.metadata.sorted_tables:
        enc_cols = [c.name for c in table.columns if isinstance(c.type, EncryptedString)]
        if not enc_cols:
            continue
        pk_cols = [c.name for c in table.primary_key.columns]
        columns: list[Column[Any]] = [
            Column(c.name, c.type, primary_key=True) for c in table.primary_key.columns
        ]
        columns += [Column(name, LargeBinary) for name in enc_cols]
        targets.append((Table(table.name, MetaData(), *columns), pk_cols, enc_cols))
    return targets


def rotate(old_secret: str, new_secret: str | None = None) -> int:
    """Re-encrypt every EncryptedString column from ``old_secret`` to ``new_secret``.

    ``new_secret`` defaults to the currently-active key. Returns the number of
    rows touched. Raises ``cryptography``'s ``InvalidTag`` if ``old_secret`` is
    wrong for a value — nothing in that table is committed (the session rolls
    back), so a bad key fails loudly instead of corrupting data.
    """
    new = new_secret if new_secret is not None else active_key_secret()
    targets = _encrypted_targets()
    rows_touched = 0
    for table, pk_cols, enc_cols in targets:
        with session() as s:
            for row in s.execute(select(table)).mappings().all():
                changes = {
                    c: encrypt_string(decrypt_string(bytes(row[c]), secret=old_secret), secret=new)
                    for c in enc_cols
                    if row[c] is not None
                }
                if changes:
                    where = and_(*[table.c[pk] == row[pk] for pk in pk_cols])
                    s.execute(update(table).where(where).values(**changes))
                    rows_touched += 1
    log.info(
        "rotate_encryption_key: re-encrypted %d rows across %d tables",
        rows_touched,
        len(targets),
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
