"""update policies

Revision ID: 4322ff468239
Revises: 0029
Create Date: 2026-06-15 16:49:12.672004+00:00
"""
from __future__ import annotations

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = '4322ff468239'
down_revision: str | None = '0029'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Fresh installs get the table from 0001's ``create_all`` — guard so this
    # migration is a no-op there (same pattern as 0025/0026).
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "update_policies" in set(inspector.get_table_names()):
        return

    op.create_table(
        'update_policies',
        sa.Column('path', sa.Text(), nullable=False),
        sa.Column('kind', sa.Text(), nullable=False),
        sa.Column('ingestion_auto_update_disabled', sa.Boolean(), nullable=True),
        sa.Column('update_instruction', sa.Text(), nullable=True),
        sa.Column('updated_by_user_id', sa.Text(), nullable=True),
        sa.Column('created_at', sa.Text(), server_default=sa.text("to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS')"), nullable=False),
        sa.Column('updated_at', sa.Text(), server_default=sa.text("to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS')"), nullable=False),
        sa.CheckConstraint("kind IN ('page', 'folder')", name='update_policies_kind_check'),
        sa.ForeignKeyConstraint(['updated_by_user_id'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('path'),
    )


def downgrade() -> None:
    op.drop_table('update_policies')
