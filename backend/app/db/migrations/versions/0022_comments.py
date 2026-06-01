"""add comments table for human comments on wiki pages

Revision ID: 0022
Revises: 0021
Create Date: 2026-06-01 00:00:00.000000
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision: str = "0022"
down_revision: str = "0021"
branch_labels = None
depends_on = None

_NOW = sa.text("to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS')")


def upgrade() -> None:
    # create_all in 0001 already materializes this table on fresh installs;
    # skip if it exists so upgrading existing DBs and fresh installs both work.
    bind = op.get_bind()
    if sa.inspect(bind).has_table("comments"):
        return
    op.create_table(
        "comments",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("doc_path", sa.Text, nullable=False),
        sa.Column("thread_root_id", sa.Text, nullable=False),
        sa.Column(
            "parent_id",
            sa.Text,
            sa.ForeignKey("comments.id", ondelete="CASCADE"),
        ),
        sa.Column("scope", sa.Text, nullable=False, server_default=sa.text("'inline'")),
        sa.Column("anchor_sha", sa.Text),
        sa.Column("start_offset", sa.Integer),
        sa.Column("end_offset", sa.Integer),
        sa.Column("quoted_text", sa.Text),
        sa.Column("author_kind", sa.Text, nullable=False, server_default=sa.text("'user'")),
        sa.Column(
            "author_user_id",
            sa.Text,
            sa.ForeignKey("users.id", ondelete="SET NULL"),
        ),
        sa.Column("body", sa.Text, nullable=False),
        sa.Column("status", sa.Text, nullable=False, server_default=sa.text("'open'")),
        sa.Column(
            "resolved_by_user_id",
            sa.Text,
            sa.ForeignKey("users.id", ondelete="SET NULL"),
        ),
        sa.Column("resolved_at", sa.Text),
        sa.Column("created_at", sa.Text, nullable=False, server_default=_NOW),
        sa.Column("updated_at", sa.Text, nullable=False, server_default=_NOW),
        sa.CheckConstraint("scope IN ('inline', 'page')", name="comments_scope_check"),
        sa.CheckConstraint(
            "author_kind IN ('user', 'agent')", name="comments_author_kind_check"
        ),
        sa.CheckConstraint(
            "status IN ('open', 'resolved', 'orphaned')", name="comments_status_check"
        ),
        sa.CheckConstraint(
            "scope <> 'inline' OR parent_id IS NOT NULL OR "
            "(anchor_sha IS NOT NULL AND start_offset IS NOT NULL "
            "AND end_offset IS NOT NULL)",
            name="comments_inline_root_anchored",
        ),
    )
    op.create_index("idx_comments_doc_status", "comments", ["doc_path", "status"])
    op.create_index("idx_comments_thread", "comments", ["thread_root_id"])


def downgrade() -> None:
    op.drop_index("idx_comments_thread", table_name="comments")
    op.drop_index("idx_comments_doc_status", table_name="comments")
    op.drop_table("comments")
