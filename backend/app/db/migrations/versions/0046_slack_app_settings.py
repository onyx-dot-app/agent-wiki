"""slack app settings singleton

Adds ``slack_app_settings``: the agent-wiki Slack app's OAuth client id and
encrypted client secret, configured from /admin/slack.

Guarded on the live inspector because ``0001_initial`` runs
``Base.metadata.create_all`` against the current model registry, so a fresh
database already has the table.

Revision ID: 0046
Revises: 0045
Create Date: 2026-07-01 00:00:00.000000+00:00
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0046"
down_revision: str | None = "0045"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    if "slack_app_settings" not in sa.inspect(op.get_bind()).get_table_names():
        op.create_table(
            "slack_app_settings",
            sa.Column("id", sa.Integer, primary_key=True, autoincrement=False),
            sa.Column("client_id", sa.Text, nullable=False, server_default=sa.text("''")),
            # Secret — AES-GCM encrypted at rest (app/db/crypto.py:EncryptedString).
            sa.Column("client_secret", sa.LargeBinary, nullable=False),
            sa.Column(
                "updated_at",
                sa.Text,
                nullable=False,
                server_default=sa.text(
                    "to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS')"
                ),
            ),
        )


def downgrade() -> None:
    op.drop_table("slack_app_settings")
