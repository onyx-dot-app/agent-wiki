"""Re-encrypt every ``EncryptedString`` column from an old key to the current one.

WHAT IT DOES
    For each secret column it reads the raw ``nonce || ciphertext`` bytes — via a
    Core table typed as ``LargeBinary`` so the ``EncryptedString`` decorator's
    auto-encrypt/decrypt is bypassed — decrypts with the OLD key, re-encrypts with
    the NEW (currently-active) key, and writes the bytes back. NULLs are skipped.
    The target columns are discovered from the ORM (every ``EncryptedString``
    column), so a newly added secret column is rotated automatically.

    - NEW key  = the active key in this process: ``ENCRYPTION_KEY_SECRET`` if set,
      else ``SECRET_KEY``.
    - OLD key  = ``OLD_ENCRYPTION_KEY_SECRET`` (required): whatever the data is
      *currently* encrypted under — the previous ``ENCRYPTION_KEY_SECRET``, or
      ``SECRET_KEY`` if you're introducing a dedicated encryption key for the
      first time (the "split").

WHY ORDER MATTERS — RUN IT IN A MAINTENANCE WINDOW
    At any instant there is exactly ONE active key, and a process can only decrypt
    data that is under *its* key. A rotation flips two things that cannot be made
    atomic: the *data* (old -> new, done by this script) and the *running
    processes* (old -> new, done by restarting them with the new key). Whenever
    those two disagree, reads of the secret columns fail with ``InvalidTag`` and
    LLM / Slack-webhook features break.

    In particular, do NOT just "set the new key and restart the app, then rotate":
    the moment the app comes up on the new key it can't read the still-old data.
    Instead, take a short window where no app process is reading these columns:

        1. Put the NEW value in the secret store, but do NOT restart the app yet
           (running pods keep the OLD key in memory and keep working).
        2. Stop / scale the backend + workers to zero (no live readers).
        3. Run this script with OLD_ENCRYPTION_KEY_SECRET=<old> and the env's
           ENCRYPTION_KEY_SECRET=<new>:

               OLD_ENCRYPTION_KEY_SECRET=<old> ENCRYPTION_KEY_SECRET=<new> \\
                   python -m app.scripts.rotate_encryption_key

        4. Start the backend + workers back up on the NEW key.

    The dataset is tiny (singleton settings tables + a handful of webhooks), so
    the script is sub-second and the window is short — but it IS a brief outage of
    LLM/Slack features, not a seamless rotation. (A seamless option would require
    try-new-then-old "dual-key" decryption during a grace period; not implemented.)

NOTES
    - ``ENCRYPTION_KEY_SECRET``, when set, must be >= 32 chars (enforced by
      ``app.config.verify_secret_key`` at startup).
    - A wrong OLD key raises ``InvalidTag`` and that table's transaction rolls
      back — a bad key fails loudly rather than corrupting data. Safe to re-run.
    - ``launcher_tokens`` are intentionally NOT rotated: they self-heal by
      re-minting when their ciphertext fails to decrypt (see
      ``app/db/launcher_tokens.py``).
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
    from app.utils.logging import setup_logging  # noqa: PLC0415

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
