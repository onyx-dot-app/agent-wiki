"""persist mcp sessions and subscriptions

Revision ID: a8189dfc6336
Revises: 0025
Create Date: 2026-06-03 20:50:18.254997+00:00
"""
from __future__ import annotations

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = 'a8189dfc6336'
down_revision: str | None = '0025'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'mcp_sessions',
        sa.Column('id', sa.Text(), nullable=False),
        sa.Column('user_id', sa.Text(), nullable=False),
        sa.Column('is_admin', sa.Boolean(), server_default=sa.text('FALSE'), nullable=False),
        sa.Column('initialized', sa.Boolean(), server_default=sa.text('FALSE'), nullable=False),
        sa.Column(
            'created_at',
            sa.Text(),
            server_default=sa.text("to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS')"),
            nullable=False,
        ),
        sa.Column(
            'last_used_at',
            sa.Text(),
            server_default=sa.text("to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS')"),
            nullable=False,
        ),
        sa.Column('expires_at', sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('idx_mcp_sessions_expires', 'mcp_sessions', ['expires_at'], unique=False)
    op.create_index('idx_mcp_sessions_user', 'mcp_sessions', ['user_id'], unique=False)

    op.create_table(
        'mcp_path_subscriptions',
        sa.Column('session_id', sa.Text(), nullable=False),
        sa.Column('rel_path', sa.Text(), nullable=False),
        sa.Column(
            'created_at',
            sa.Text(),
            server_default=sa.text("to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS')"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(['session_id'], ['mcp_sessions.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('session_id', 'rel_path'),
    )
    op.create_index('idx_mcp_path_subs_path', 'mcp_path_subscriptions', ['rel_path'], unique=False)

    op.create_table(
        'mcp_job_subscriptions',
        sa.Column('session_id', sa.Text(), nullable=False),
        sa.Column('job_id', sa.Text(), nullable=False),
        sa.Column(
            'created_at',
            sa.Text(),
            server_default=sa.text("to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS')"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(['session_id'], ['mcp_sessions.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('session_id', 'job_id'),
    )
    op.create_index('idx_mcp_job_subs_job', 'mcp_job_subscriptions', ['job_id'], unique=False)


def downgrade() -> None:
    op.drop_index('idx_mcp_job_subs_job', table_name='mcp_job_subscriptions')
    op.drop_table('mcp_job_subscriptions')
    op.drop_index('idx_mcp_path_subs_path', table_name='mcp_path_subscriptions')
    op.drop_table('mcp_path_subscriptions')
    op.drop_index('idx_mcp_sessions_user', table_name='mcp_sessions')
    op.drop_index('idx_mcp_sessions_expires', table_name='mcp_sessions')
    op.drop_table('mcp_sessions')
