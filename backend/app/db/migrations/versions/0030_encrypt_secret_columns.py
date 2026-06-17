"""encrypt provider/secret columns at rest

Converts the plaintext secret columns on the singleton settings tables from
``text`` to AES-GCM-encrypted ``bytea`` (``app/db/crypto.py:EncryptedString``):

- ``llm_settings``: anthropic_api_key, openai_api_key, gemini_api_key, custom_api_key
- ``web_settings``: serper_api_key, firecrawl_api_key
- ``ingest_settings``: api_key (nullable)
- ``braintrust_settings``: api_key

Each column is migrated in place — its name is preserved. Because Postgres can't
cast ``text`` -> ``bytea`` with the per-value encrypt step inline, we add a
scratch ``bytea`` column, encrypt every existing value into it in Python, drop
the old column, and rename the scratch column back. The settings tables are
singletons (one row at most), so the data step is trivial in volume.

Fresh installs get these columns as ``bytea`` directly from ``0001_initial``'s
``Base.metadata.create_all`` against the current model registry, so each column
is guarded on the live inspector and skipped when it's already ``bytea`` (same
no-op-on-fresh-DB pattern as the other post-0001 migrations).

Encryption is keyed by ``SECRET_KEY`` (see ``app/db/crypto.py``); it must be set
and stable across this migration and all later reads, exactly as for the
already-encrypted ``slack_webhooks.webhook_url``.

Revision ID: 0030
Revises: 4322ff468239
Create Date: 2026-06-17 00:00:00.000000+00:00
"""
from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op

from app.db.crypto import decrypt_string, encrypt_string


revision: str = "0030"
down_revision: str | None = "4322ff468239"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# (table, column, nullable) for every secret column being encrypted.
_SECRET_COLUMNS: list[tuple[str, str, bool]] = [
    ("llm_settings", "anthropic_api_key", False),
    ("llm_settings", "openai_api_key", False),
    ("llm_settings", "gemini_api_key", False),
    ("llm_settings", "custom_api_key", False),
    ("web_settings", "serper_api_key", False),
    ("web_settings", "firecrawl_api_key", False),
    ("ingest_settings", "api_key", True),
    ("braintrust_settings", "api_key", False),
]


def _column_is_bytea(bind: sa.engine.Connection, table: str, column: str) -> bool:
    inspector = sa.inspect(bind)
    if table not in inspector.get_table_names():
        # Table not created yet — nothing for this migration to do.
        return True
    for col in inspector.get_columns(table):
        if col["name"] == column:
            return isinstance(col["type"], sa.LargeBinary)
    return True  # column absent — leave it to create_all


def upgrade() -> None:
    bind = op.get_bind()
    for table, column, nullable in _SECRET_COLUMNS:
        if _column_is_bytea(bind, table, column):
            continue  # fresh install already has bytea

        scratch = f"_{column}_enc"
        op.add_column(table, sa.Column(scratch, sa.LargeBinary, nullable=True))
        rows = bind.execute(
            sa.text(f'SELECT id, "{column}" AS val FROM {table}')
        ).fetchall()
        for row_id, value in rows:
            if value is None:
                continue  # preserve NULL (nullable columns)
            bind.execute(
                sa.text(f'UPDATE {table} SET "{scratch}" = :v WHERE id = :id'),
                {"v": encrypt_string(value), "id": row_id},
            )
        op.drop_column(table, column)
        op.alter_column(table, scratch, new_column_name=column)
        if not nullable:
            op.alter_column(table, column, nullable=False)


def downgrade() -> None:
    bind = op.get_bind()
    for table, column, nullable in _SECRET_COLUMNS:
        if not _column_is_bytea(bind, table, column):
            continue  # already text (or absent)

        scratch = f"_{column}_txt"
        op.add_column(table, sa.Column(scratch, sa.Text, nullable=True))
        rows = bind.execute(
            sa.text(f'SELECT id, "{column}" AS val FROM {table}')
        ).fetchall()
        for row_id, value in rows:
            if value is None:
                continue
            bind.execute(
                sa.text(f'UPDATE {table} SET "{scratch}" = :v WHERE id = :id'),
                {"v": decrypt_string(bytes(value)), "id": row_id},
            )
        op.drop_column(table, column)
        op.alter_column(table, scratch, new_column_name=column)
        if not nullable:
            op.alter_column(
                table, column, nullable=False, server_default=sa.text("''")
            )
