"""destination configs registry

Adds the per-user ``destination_configs`` table — a typed, named delivery
target a trigger can fire to, generalizing ``slack_webhooks`` to any
destination type. ``type`` is a ``trigger_destinations`` catalog slug,
``config_json`` holds the non-secret per-type settings, and ``secret`` is the
optional AES-GCM-encrypted credential (``app/db/crypto.py:EncryptedString``).

The table create is guarded on the live inspector because ``0001_initial``
runs ``Base.metadata.create_all`` against the current model registry, so a
fresh database already has the table (same pattern as ``0023_slack_destination``).

Revision ID: 0039
Revises: 0038
Create Date: 2026-06-24 00:00:00.000000+00:00
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB


revision: str = "0039"
down_revision: str | None = "0038"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if "destination_configs" not in inspector.get_table_names():
        op.create_table(
            "destination_configs",
            sa.Column("id", sa.Text, primary_key=True),
            sa.Column(
                "owner_user_id",
                sa.Text,
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("type", sa.Text, nullable=False),
            sa.Column("name", sa.Text, nullable=False),
            sa.Column(
                "config_json",
                JSONB,
                nullable=False,
                server_default=sa.text("'{}'::jsonb"),
            ),
            # Secret — AES-GCM encrypted at rest (app/db/crypto.py:EncryptedString).
            sa.Column("secret", sa.LargeBinary, nullable=True),
            sa.Column(
                "created_at",
                sa.Text,
                nullable=False,
                server_default=sa.text(
                    "to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS')"
                ),
            ),
        )
        op.create_index("idx_destination_configs_owner", "destination_configs", ["owner_user_id"])


def downgrade() -> None:
    op.drop_index("idx_destination_configs_owner", table_name="destination_configs")
    op.drop_table("destination_configs")
