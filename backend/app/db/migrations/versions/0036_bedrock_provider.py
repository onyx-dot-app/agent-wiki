"""bedrock-provider

Adds AWS Bedrock (Converse API) provider config to ``llm_settings``:

- ``bedrock_aws_region``, ``bedrock_endpoint_url`` (Text config)
- ``bedrock_aws_access_key_id``, ``bedrock_aws_secret_access_key``,
  ``bedrock_aws_session_token`` (EncryptedString / ``bytea`` secrets)

Guarded adds — ``0001``'s ``create_all`` already builds these on fresh installs,
so each column is skipped when present. On an existing install the secret
columns are added nullable, the singleton row is backfilled with an encrypted
empty string (a literal ``b''`` would crash on decrypt), then tightened to NOT
NULL — the same storage contract as the other EncryptedString secret columns.

Revision ID: 0036
Revises: 0035
Create Date: 2026-06-24 00:00:00.000000+00:00
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op

from app.db.crypto import encrypt_string

revision: str = "0036"
down_revision: str | None = "0035"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_TEXT_COLUMNS = ("bedrock_aws_region", "bedrock_endpoint_url")
_SECRET_COLUMNS = (
    "bedrock_aws_access_key_id",
    "bedrock_aws_secret_access_key",
    "bedrock_aws_session_token",
)


def _existing_columns(bind: sa.engine.Connection) -> set[str]:
    return {col["name"] for col in sa.inspect(bind).get_columns("llm_settings")}


def upgrade() -> None:
    bind = op.get_bind()
    cols = _existing_columns(bind)

    for name in _TEXT_COLUMNS:
        if name not in cols:
            op.add_column(
                "llm_settings",
                sa.Column(name, sa.Text, nullable=False, server_default=sa.text("''")),
            )

    to_add = [name for name in _SECRET_COLUMNS if name not in cols]
    for name in to_add:
        op.add_column("llm_settings", sa.Column(name, sa.LargeBinary, nullable=True))
    if to_add:
        empty = encrypt_string("")
        set_clause = ", ".join(f'"{name}" = :{name}' for name in to_add)
        bind.execute(
            sa.text(f"UPDATE llm_settings SET {set_clause} WHERE id = 1"),
            {name: empty for name in to_add},
        )
        for name in to_add:
            op.alter_column("llm_settings", name, nullable=False)


def downgrade() -> None:
    for name in (*_SECRET_COLUMNS, *_TEXT_COLUMNS):
        op.drop_column("llm_settings", name)
