"""bedrock-bearer-token

Adds the AWS Bedrock API key (bearer token) credential to ``llm_settings``:
``bedrock_aws_bearer_token`` (EncryptedString / ``bytea`` secret). Same guarded
add-nullable / backfill-encrypted-empty / set-NOT-NULL shape as the other
EncryptedString secret columns (a literal ``b''`` would crash on decrypt).

Revision ID: 0037
Revises: 0036
Create Date: 2026-06-29 00:00:00.000000+00:00
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op

from app.db.crypto import encrypt_string

revision: str = "0037"
down_revision: str | None = "0036"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_COLUMN = "bedrock_aws_bearer_token"


def upgrade() -> None:
    bind = op.get_bind()
    cols = {col["name"] for col in sa.inspect(bind).get_columns("llm_settings")}
    if _COLUMN in cols:
        return  # fresh install already has it from 0001's create_all
    op.add_column("llm_settings", sa.Column(_COLUMN, sa.LargeBinary, nullable=True))
    bind.execute(
        sa.text(f'UPDATE llm_settings SET "{_COLUMN}" = :v WHERE id = 1'),
        {"v": encrypt_string("")},
    )
    op.alter_column("llm_settings", _COLUMN, nullable=False)


def downgrade() -> None:
    op.drop_column("llm_settings", _COLUMN)
