"""slack connection muted flag

Pauses Slack delivery without disconnecting. Guarded with the inspector
because ``0001_initial`` builds fresh databases from the current models.

Revision ID: 7f2a91c04b11
Revises: 0075dcbb622e
Create Date: 2026-07-07 05:20:00.000000+00:00
"""
from __future__ import annotations

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "7f2a91c04b11"
down_revision: str | None = "0075dcbb622e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    cols = {c["name"] for c in inspector.get_columns("user_slack_connections")}
    if "muted" not in cols:
        op.add_column(
            "user_slack_connections",
            sa.Column(
                "muted",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("FALSE"),
            ),
        )


def downgrade() -> None:
    op.drop_column("user_slack_connections", "muted")
