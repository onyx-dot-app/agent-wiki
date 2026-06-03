"""recent doc views

Revision ID: 0024
Revises: 0023
Create Date: 2026-06-03
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0024"
down_revision: str | None = "0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Fresh installs get the table from 0001's ``create_all`` — guard so
    # this migration is a no-op there (same pattern as 0014).
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "recent_doc_views" in set(inspector.get_table_names()):
        return

    op.create_table(
        "recent_doc_views",
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column(
            "viewed_at",
            sa.Text(),
            server_default=sa.text("to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS')"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id", "path"),
    )
    op.create_index(
        "ix_recent_doc_views_user_viewed",
        "recent_doc_views",
        ["user_id", "viewed_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_recent_doc_views_user_viewed", table_name="recent_doc_views")
    op.drop_table("recent_doc_views")
