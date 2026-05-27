"""wiki_edit_drafts

Revision ID: 0022
Revises: 0021
Create Date: 2026-05-27

"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0022"
down_revision: str | tuple[str, ...] = ("0021",)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "wiki_edit_drafts",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column("base_sha", sa.Text(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.Text(),
            server_default=sa.text("(now() AT TIME ZONE 'UTC')::text"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.Text(),
            server_default=sa.text("(now() AT TIME ZONE 'UTC')::text"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("path", "user_id"),
    )


def downgrade() -> None:
    op.drop_table("wiki_edit_drafts")
