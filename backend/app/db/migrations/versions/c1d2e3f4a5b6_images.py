"""images

Adds ``images``: binary wiki image blobs anchored to stable doc ids.
Guarded with the inspector because ``0001_initial`` builds fresh
databases from the current models.

Revision ID: c1d2e3f4a5b6
Revises: c2e7a4d9f1b8
Create Date: 2026-07-24 19:00:00.000000+00:00
"""
from __future__ import annotations

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "c1d2e3f4a5b6"
down_revision: str | None = "c2e7a4d9f1b8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if inspector.has_table("images"):
        return
    op.create_table(
        "images",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("sha256", sa.Text(), nullable=False),
        sa.Column("content_type", sa.Text(), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("data", sa.LargeBinary(), nullable=False),
        sa.Column("anchor_doc_id", sa.Text(), nullable=False),
        sa.Column("uploaded_by", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.Text(),
            server_default=sa.text("to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS')"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["uploaded_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_images_anchor_doc_id", "images", ["anchor_doc_id"])


def downgrade() -> None:
    op.drop_index("idx_images_anchor_doc_id", table_name="images")
    op.drop_table("images")
