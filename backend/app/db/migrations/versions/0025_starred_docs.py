"""starred docs

Revision ID: 0025
Revises: 0024
Create Date: 2026-06-03
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0025"
down_revision: str | None = "0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Fresh installs get the table from 0001's ``create_all`` — guard so
    # this migration is a no-op there (same pattern as 0014/0024).
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "starred_docs" in set(inspector.get_table_names()):
        return

    op.create_table(
        "starred_docs",
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id", "path"),
    )
    op.create_index(
        "ix_starred_docs_user_sort",
        "starred_docs",
        ["user_id", "sort_order"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_starred_docs_user_sort", table_name="starred_docs")
    op.drop_table("starred_docs")
