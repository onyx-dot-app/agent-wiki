"""coedit_ops: append-only edit-operation log for co-edit sessions

Revision ID: 0040
Revises: 0039
Create Date: 2026-06-30 00:00:00.000000+00:00
"""
from __future__ import annotations

from typing import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "0040"
down_revision: str | None = "0039"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # Fresh installs get this table from 0001's create_all — guard so this is a
    # no-op there and only does real work on databases created before it.
    if "coedit_ops" not in set(inspector.get_table_names()):
        op.create_table(
            "coedit_ops",
            sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
            sa.Column("session_id", sa.BigInteger(), nullable=False),
            sa.Column("seq", sa.BigInteger(), nullable=False),
            sa.Column("author_user_id", sa.Text(), nullable=False),
            sa.Column("base_version", sa.BigInteger(), nullable=False),
            sa.Column("op_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
            sa.Column(
                "created_at",
                sa.Text(),
                server_default=sa.text(
                    "to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS')"
                ),
                nullable=False,
            ),
            sa.ForeignKeyConstraint(
                ["session_id"], ["coedit_sessions.id"], ondelete="CASCADE"
            ),
            sa.ForeignKeyConstraint(["author_user_id"], ["users.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("session_id", "seq", name="idx_coedit_ops_session_seq"),
        )


def downgrade() -> None:
    op.drop_table("coedit_ops")
