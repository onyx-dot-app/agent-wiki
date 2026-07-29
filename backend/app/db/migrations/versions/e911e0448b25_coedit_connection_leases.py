"""coedit_connections: process-shared WebSocket leases.

Revision ID: e911e0448b25
Revises: d7f2c8b4a1e6
Create Date: 2026-07-29 22:42:41.915972+00:00
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "e911e0448b25"
down_revision: str | None = "d7f2c8b4a1e6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 0001 materializes current metadata for a fresh install, so this migration
    # only has work to do for databases that predate connection leases.
    if "coedit_connections" in set(sa.inspect(op.get_bind()).get_table_names()):
        return
    op.create_table(
        "coedit_connections",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("session_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.Text(), nullable=False),
        sa.Column(
            "connected_at",
            sa.Text(),
            server_default=sa.text("to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS')"),
            nullable=False,
        ),
        sa.Column(
            "last_seen_at",
            sa.Text(),
            server_default=sa.text("to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS')"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["session_id", "user_id"],
            ["coedit_participants.session_id", "coedit_participants.user_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "idx_coedit_connections_last_seen",
        "coedit_connections",
        ["last_seen_at"],
    )
    op.create_index(
        "idx_coedit_connections_session_user",
        "coedit_connections",
        ["session_id", "user_id"],
    )


def downgrade() -> None:
    op.drop_table("coedit_connections")
