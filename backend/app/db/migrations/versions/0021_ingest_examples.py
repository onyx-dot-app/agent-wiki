"""add ingest_eval_samples table for eval logging

Revision ID: 0021
Revises: 0020
Create Date: 2026-05-21 00:00:00.000000
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision: str = "0021"
down_revision: str = "0020"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "ingest_eval_samples",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "created_at",
            sa.Text,
            nullable=False,
            server_default=sa.text("to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS')"),
        ),
        sa.Column("source_type", sa.Text),
        sa.Column("source_title", sa.Text),
        sa.Column("source_url", sa.Text),
        sa.Column("source_content", sa.Text, nullable=False),
        sa.Column("wiki_path", sa.Text, nullable=False),
        sa.Column("wiki_body_before", sa.Text, nullable=False),
        sa.Column("diff", sa.Text),
        sa.Column("outcome", sa.Text, nullable=False),
        sa.Column("commit_sha", sa.Text),
    )
    op.create_index("ix_ingest_eval_samples_created_at", "ingest_eval_samples", ["created_at"])
    op.create_index("ix_ingest_eval_samples_outcome", "ingest_eval_samples", ["outcome"])


def downgrade() -> None:
    op.drop_index("ix_ingest_eval_samples_outcome", table_name="ingest_eval_samples")
    op.drop_index("ix_ingest_eval_samples_created_at", table_name="ingest_eval_samples")
    op.drop_table("ingest_eval_samples")
