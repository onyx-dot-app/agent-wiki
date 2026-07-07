"""ingest_settings updated_by_user_id

Records which admin last saved the ingest settings or regenerated the
API key — previously the actor only existed in an ephemeral log line.
Guarded with the inspector because ``0001_initial`` builds fresh
databases from the current models, which already include the column.

Revision ID: 0076
Revises: 0075dcbb622e
Create Date: 2026-07-07
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0076"
down_revision: str | None = "0075dcbb622e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    cols = {c["name"] for c in sa.inspect(bind).get_columns("ingest_settings")}
    if "updated_by_user_id" not in cols:
        op.add_column(
            "ingest_settings",
            sa.Column("updated_by_user_id", sa.Text(), nullable=True),
        )
        op.create_foreign_key(
            "ingest_settings_updated_by_user_id_fkey",
            "ingest_settings",
            "users",
            ["updated_by_user_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    op.drop_column("ingest_settings", "updated_by_user_id")
