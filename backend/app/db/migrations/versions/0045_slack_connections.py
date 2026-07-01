"""slack connection tables

Adds ``user_slack_connections`` (one encrypted bot token per user per
workspace) and ``slack_connect_states`` (single-use CSRF state for the
Connect-Slack handshake).

Guarded on the live inspector because ``0001_initial`` runs
``Base.metadata.create_all`` against the current model registry, so a fresh
database already has both tables.

Revision ID: 0045
Revises: 0044
Create Date: 2026-07-01 00:00:00.000000+00:00
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0045"
down_revision: str | None = "0044"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NOW = sa.text("to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS')")


def upgrade() -> None:
    tables = set(sa.inspect(op.get_bind()).get_table_names())

    if "user_slack_connections" not in tables:
        op.create_table(
            "user_slack_connections",
            sa.Column(
                "user_id",
                sa.Text,
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                primary_key=True,
            ),
            sa.Column("team_id", sa.Text, primary_key=True),
            sa.Column("team_name", sa.Text, nullable=True),
            sa.Column("slack_user_id", sa.Text, nullable=False),
            # Secret — AES-GCM encrypted at rest (app/db/crypto.py:EncryptedString).
            sa.Column("bot_token", sa.LargeBinary, nullable=False),
            sa.Column("token_display", sa.Text, nullable=False),
            sa.Column("scope", sa.Text, nullable=True),
            sa.Column("created_at", sa.Text, nullable=False, server_default=_NOW),
            sa.Column("updated_at", sa.Text, nullable=False, server_default=_NOW),
        )

    if "slack_connect_states" not in tables:
        op.create_table(
            "slack_connect_states",
            sa.Column("state", sa.Text, primary_key=True),
            sa.Column(
                "user_id",
                sa.Text,
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("return_to", sa.Text, nullable=True),
            sa.Column("expires_at", sa.Text, nullable=False),
            sa.Column("consumed_at", sa.Text, nullable=True),
        )


def downgrade() -> None:
    op.drop_table("slack_connect_states")
    op.drop_table("user_slack_connections")
