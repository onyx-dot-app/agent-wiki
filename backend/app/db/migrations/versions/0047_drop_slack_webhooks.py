"""drop the slack_webhooks table

Slack channels live in ``destination_configs`` (webhook-secret, channel, or
DM targets); the standalone webhook store has no remaining readers. The boot
reconcile mirrored every stored channel into a destination config before this
migration ships, so the rows are redundant copies.

Guarded on the live inspector because ``0001_initial`` builds fresh databases
from the current model registry, which no longer has the table.

Revision ID: 0047
Revises: 0046
Create Date: 2026-07-02 00:00:00.000000+00:00
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0047"
down_revision: str | None = "0046"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    if "slack_webhooks" in sa.inspect(op.get_bind()).get_table_names():
        op.drop_table("slack_webhooks")


def downgrade() -> None:
    # The table's data is not recoverable; recreate empty for schema parity.
    op.create_table(
        "slack_webhooks",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column(
            "owner_user_id",
            sa.Text,
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.Text, nullable=False),
        # Secret — AES-GCM encrypted at rest (app/db/crypto.py:EncryptedString).
        sa.Column("webhook_url", sa.LargeBinary, nullable=False),
        sa.Column(
            "created_at",
            sa.Text,
            nullable=False,
            server_default=sa.text("to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS')"),
        ),
    )
    op.create_index("idx_slack_webhooks_owner", "slack_webhooks", ["owner_user_id"])
