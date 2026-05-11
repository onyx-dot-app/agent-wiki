"""documents_fts: add indexed_sha for reconciliation sweep

Records the git sha that was indexed into BM25 so the hourly reconcile
task can detect drift (lost pgmq messages, worker crashes mid-handler)
and enqueue catch-up reindexes. Existing rows get NULL and are picked
up as stale on the first sweep — the index converges within an hour
without operator action.

Revision ID: 0012
Revises: 0011
Create Date: 2026-05-10 00:12:00.000000+00:00
"""
from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {c["name"] for c in inspector.get_columns("documents_fts")}
    if "indexed_sha" not in columns:
        op.add_column(
            "documents_fts",
            sa.Column("indexed_sha", sa.Text(), nullable=True),
        )


def downgrade() -> None:
    op.drop_column("documents_fts", "indexed_sha")
