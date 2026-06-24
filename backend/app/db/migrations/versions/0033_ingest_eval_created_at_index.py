"""ensure ix_ingest_eval_samples_created_at exists

The retention sweep filters on created_at; the index must exist for it to be a
range scan rather than a seq scan. Fresh installs materialize the table via
0001's create_all and 0021 then early-returns, so the index could be missing
depending on install path. Create it idempotently to cover every DB.

Revision ID: 0033
Revises: 0032
Create Date: 2026-06-24 00:00:00.000000
"""
from __future__ import annotations

from alembic import op

revision: str = "0033"
down_revision: str = "0032"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_ingest_eval_samples_created_at",
        "ingest_eval_samples",
        ["created_at"],
        if_not_exists=True,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_ingest_eval_samples_created_at",
        table_name="ingest_eval_samples",
        if_exists=True,
    )
