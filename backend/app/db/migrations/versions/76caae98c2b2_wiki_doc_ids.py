"""wiki doc ids

Adds ``wiki_doc_ids`` — the stable path↔id mapping for wiki pages and
folders. Guarded with the inspector because ``0001_initial`` builds fresh
databases from the current models.

Revision ID: 76caae98c2b2
Revises: b7f3c1a9d2e4
Create Date: 2026-07-10 20:56:21.462534+00:00
"""
from __future__ import annotations

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = '76caae98c2b2'
down_revision: str | None = 'b7f3c1a9d2e4'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if inspector.has_table("wiki_doc_ids"):
        return
    op.create_table(
        'wiki_doc_ids',
        sa.Column('id', sa.Text(), nullable=False),
        sa.Column('path', sa.Text(), nullable=False),
        sa.Column('kind', sa.Text(), nullable=False),
        sa.Column(
            'created_at',
            sa.Text(),
            server_default=sa.text("to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS')"),
            nullable=False,
        ),
        sa.Column('deleted_at', sa.Text(), nullable=True),
        sa.CheckConstraint("kind IN ('page', 'folder')", name='wiki_doc_ids_kind_check'),
        sa.PrimaryKeyConstraint('id'),
    )
    # Unique among live rows only: tombstone rows (deleted_at set) may share
    # a path with each other and with a later live row at the same path.
    op.create_index(
        'uq_wiki_doc_ids_live_path',
        'wiki_doc_ids',
        ['path'],
        unique=True,
        postgresql_where=sa.text('deleted_at IS NULL'),
    )


def downgrade() -> None:
    op.drop_index(
        'uq_wiki_doc_ids_live_path',
        table_name='wiki_doc_ids',
        postgresql_where=sa.text('deleted_at IS NULL'),
    )
    op.drop_table('wiki_doc_ids')
