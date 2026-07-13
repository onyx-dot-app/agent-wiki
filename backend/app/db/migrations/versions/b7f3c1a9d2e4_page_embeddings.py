"""page embeddings

Adds ``page_embeddings`` — the per-page embedding-vector store for the
ingestion relevance filter (Phase 0). Durable storage only; scoring runs
against an in-worker matrix built from these rows. Guarded with the inspector
because ``0001_initial`` builds fresh databases from the current models.

Revision ID: b7f3c1a9d2e4
Revises: e4b8c61d9a73
Create Date: 2026-07-13 00:00:00.000000+00:00
"""
from __future__ import annotations

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = 'b7f3c1a9d2e4'
down_revision: str | None = 'e4b8c61d9a73'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if inspector.has_table("page_embeddings"):
        return
    op.create_table(
        'page_embeddings',
        sa.Column('path', sa.Text(), nullable=False),
        sa.Column('content_sha256', sa.Text(), nullable=False),
        sa.Column('model', sa.Text(), nullable=False),
        sa.Column('vector', sa.LargeBinary(), nullable=False),
        sa.Column(
            'updated_at',
            sa.Text(),
            server_default=sa.text("to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS')"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint('path'),
    )
    op.create_index('ix_page_embeddings_updated_at', 'page_embeddings', ['updated_at'])


def downgrade() -> None:
    op.drop_index('ix_page_embeddings_updated_at', table_name='page_embeddings')
    op.drop_table('page_embeddings')
