"""coedit_sessions + coedit_participants: the Postgres editing buffer

Revision ID: 0038
Revises: 0037
Create Date: 2026-06-30 00:00:00.000000+00:00
"""
from __future__ import annotations

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0038"
down_revision: str | None = "0037"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = set(inspector.get_table_names())

    # Fresh installs get these tables from 0001's create_all — guard so this
    # migration is a no-op there and only does real work on databases created
    # before co-editing landed.
    if "coedit_sessions" not in existing:
        op.create_table(
            "coedit_sessions",
            sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
            sa.Column("path", sa.Text(), nullable=False),
            sa.Column(
                "buffer_text", sa.Text(), server_default=sa.text("''"), nullable=False
            ),
            sa.Column(
                "version", sa.BigInteger(), server_default=sa.text("0"), nullable=False
            ),
            sa.Column("base_sha", sa.Text(), nullable=True),
            sa.Column(
                "status", sa.Text(), server_default=sa.text("'active'"), nullable=False
            ),
            sa.Column(
                "created_at",
                sa.Text(),
                server_default=sa.text(
                    "to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS')"
                ),
                nullable=False,
            ),
            sa.Column(
                "updated_at",
                sa.Text(),
                server_default=sa.text(
                    "to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS')"
                ),
                nullable=False,
            ),
            sa.Column("last_checkpoint_at", sa.Text(), nullable=True),
            sa.CheckConstraint(
                "status IN ('active', 'closed')", name="coedit_sessions_status_check"
            ),
            sa.PrimaryKeyConstraint("id"),
        )
        # At most one active session per page; closed sessions accumulate as
        # history without blocking a fresh one.
        op.create_index(
            "idx_coedit_sessions_active_path",
            "coedit_sessions",
            ["path"],
            unique=True,
            postgresql_where=sa.text("status = 'active'"),
        )

    if "coedit_participants" not in existing:
        op.create_table(
            "coedit_participants",
            sa.Column("session_id", sa.BigInteger(), nullable=False),
            sa.Column("user_id", sa.Text(), nullable=False),
            sa.Column(
                "joined_at",
                sa.Text(),
                server_default=sa.text(
                    "to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS')"
                ),
                nullable=False,
            ),
            sa.Column(
                "last_seen_at",
                sa.Text(),
                server_default=sa.text(
                    "to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS')"
                ),
                nullable=False,
            ),
            sa.ForeignKeyConstraint(
                ["session_id"], ["coedit_sessions.id"], ondelete="CASCADE"
            ),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("session_id", "user_id"),
        )
        op.create_index(
            "idx_coedit_participants_user", "coedit_participants", ["user_id"]
        )


def downgrade() -> None:
    op.drop_index("idx_coedit_participants_user", table_name="coedit_participants")
    op.drop_table("coedit_participants")
    op.drop_index("idx_coedit_sessions_active_path", table_name="coedit_sessions")
    op.drop_table("coedit_sessions")
