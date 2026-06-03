"""persist mcp sessions and subscriptions

Revision ID: 0027
Revises: 0026
Create Date: 2026-06-03 20:50:18.254997+00:00

Adds the ``mcp_sessions``, ``mcp_path_subscriptions``, and
``mcp_job_subscriptions`` tables backing the move from in-memory MCP
session state to Postgres so a wiki-server restart doesn't invalidate
every client's ``Mcp-Session-Id``.

Each ``op.create_table`` is guarded by ``inspector.has_table`` because
``0001_initial`` calls ``Base.metadata.create_all(bind)``, which
materializes whatever is registered on ``Base.metadata`` at the
moment 0001 runs — including these tables, on fresh databases
authored after this revision shipped. The guard makes the migration a
no-op in that case and a real CREATE for production databases that
ran 0001 before this revision existed.
"""
from __future__ import annotations

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0027"
down_revision: str | None = "0026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_NOW_TEXT_DEFAULT = sa.text(
    "to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS')"
)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not inspector.has_table('mcp_sessions'):
        op.create_table(
            'mcp_sessions',
            sa.Column('id', sa.Text(), nullable=False),
            sa.Column('user_id', sa.Text(), nullable=False),
            sa.Column('is_admin', sa.Boolean(), server_default=sa.text('FALSE'), nullable=False),
            sa.Column('initialized', sa.Boolean(), server_default=sa.text('FALSE'), nullable=False),
            sa.Column('created_at', sa.Text(), server_default=_NOW_TEXT_DEFAULT, nullable=False),
            sa.Column('last_used_at', sa.Text(), server_default=_NOW_TEXT_DEFAULT, nullable=False),
            sa.Column('expires_at', sa.Text(), nullable=False),
            sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
            sa.PrimaryKeyConstraint('id'),
        )
        op.create_index('idx_mcp_sessions_expires', 'mcp_sessions', ['expires_at'], unique=False)
        op.create_index('idx_mcp_sessions_user', 'mcp_sessions', ['user_id'], unique=False)

    if not inspector.has_table('mcp_path_subscriptions'):
        op.create_table(
            'mcp_path_subscriptions',
            sa.Column('session_id', sa.Text(), nullable=False),
            sa.Column('rel_path', sa.Text(), nullable=False),
            sa.Column('created_at', sa.Text(), server_default=_NOW_TEXT_DEFAULT, nullable=False),
            sa.ForeignKeyConstraint(['session_id'], ['mcp_sessions.id'], ondelete='CASCADE'),
            sa.PrimaryKeyConstraint('session_id', 'rel_path'),
        )
        op.create_index('idx_mcp_path_subs_path', 'mcp_path_subscriptions', ['rel_path'], unique=False)

    if not inspector.has_table('mcp_job_subscriptions'):
        op.create_table(
            'mcp_job_subscriptions',
            sa.Column('session_id', sa.Text(), nullable=False),
            sa.Column('job_id', sa.Text(), nullable=False),
            sa.Column('created_at', sa.Text(), server_default=_NOW_TEXT_DEFAULT, nullable=False),
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
