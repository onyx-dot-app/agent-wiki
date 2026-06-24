"""add bm25_score to ingest_eval_samples

Revision ID: 0032
Revises: 0031
Create Date: 2026-06-23 00:00:00.000000
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision: str = "0032"
down_revision: str = "0031"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # create_all in 0001 already materializes the current schema on fresh
    # installs; skip if the column is already present so upgrading existing DBs
    # and fresh installs both work.
    bind = op.get_bind()
    cols = {c["name"] for c in sa.inspect(bind).get_columns("ingest_eval_samples")}
    if "bm25_score" in cols:
        return
    op.add_column("ingest_eval_samples", sa.Column("bm25_score", sa.Float, nullable=True))


def downgrade() -> None:
    op.drop_column("ingest_eval_samples", "bm25_score")
