"""coedit_sessions.checkpointed_version: track the last-committed version

Revision ID: 0041
Revises: 0040
Create Date: 2026-07-01 00:00:00.000000+00:00
"""
from __future__ import annotations

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0041"
down_revision: str | None = "0040"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # Fresh installs get the column from 0001's create_all — guard so this is a
    # no-op there and only adds it on databases created before co-editing.
    cols = {c["name"] for c in inspector.get_columns("coedit_sessions")}
    if "checkpointed_version" not in cols:
        op.add_column(
            "coedit_sessions",
            sa.Column(
                "checkpointed_version",
                sa.BigInteger(),
                server_default=sa.text("0"),
                nullable=False,
            ),
        )


def downgrade() -> None:
    op.drop_column("coedit_sessions", "checkpointed_version")
