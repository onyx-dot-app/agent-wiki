"""users session_epoch

Bumped on password change so pre-change sessions stop authenticating.
Guarded with the inspector because ``0001_initial`` builds fresh databases
from the current models, which already include the column.

Revision ID: 0075dcbb622e
Revises: 0050
Create Date: 2026-07-07 01:14:24.482756+00:00
"""
from __future__ import annotations

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0075dcbb622e"
down_revision: str | None = "0050"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    cols = {c["name"] for c in inspector.get_columns("users")}
    if "session_epoch" not in cols:
        op.add_column(
            "users",
            sa.Column(
                "session_epoch",
                sa.Integer(),
                nullable=False,
                server_default=sa.text("0"),
            ),
        )


def downgrade() -> None:
    op.drop_column("users", "session_epoch")
