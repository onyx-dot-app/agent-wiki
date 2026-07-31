"""images

Adds ``images``: binary wiki image blobs anchored to stable doc ids, with the
retention state the sweep flags them by.

Revision ID: b6f1d94ac370
Revises: 4a01439ee668
Create Date: 2026-07-30 19:45:00.000000+00:00
"""
from __future__ import annotations

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "b6f1d94ac370"
down_revision: str | None = "4a01439ee668"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Guarded because ``0001_initial`` builds fresh databases from the current
    # models, which already carry this table.
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
        sa.Column("unreferenced_since", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["uploaded_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_images_anchor_doc_id", "images", ["anchor_doc_id"])


def downgrade() -> None:
    op.drop_index("idx_images_anchor_doc_id", table_name="images")
    op.drop_table("images")
