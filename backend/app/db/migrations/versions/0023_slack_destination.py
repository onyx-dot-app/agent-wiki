"""slack webhooks registry + slack trigger destination

Adds the per-user ``slack_webhooks`` table (named incoming webhooks a user
can point triggers at), a ``triggers.slack_webhook_id`` reference column, and
seeds a ``slack`` row into the ``trigger_destinations`` catalog.

The table create and column add are guarded on the live inspector because
``0001_initial`` runs ``Base.metadata.create_all`` against the current model
registry — fresh databases bootstrapped after these were added to
``models.py`` already have them (same pattern as ``0010_braintrust_settings``).
The destination seed is an idempotent ``ON CONFLICT DO NOTHING`` insert.

Revision ID: 0023
Revises: 0022
Create Date: 2026-06-02 00:00:00.000000+00:00
"""
from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0023"
down_revision: str | None = "0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if "slack_webhooks" not in inspector.get_table_names():
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
            sa.Column("webhook_url", sa.Text, nullable=False),
            sa.Column(
                "created_at",
                sa.Text,
                nullable=False,
                server_default=sa.text(
                    "to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS')"
                ),
            ),
        )
        op.create_index(
            "idx_slack_webhooks_owner", "slack_webhooks", ["owner_user_id"]
        )

    trigger_cols = {c["name"] for c in inspector.get_columns("triggers")}
    if "slack_webhook_id" not in trigger_cols:
        op.add_column("triggers", sa.Column("slack_webhook_id", sa.Text, nullable=True))

    # Seed the slack destination row (idempotent).
    bind.execute(
        sa.text(
            "INSERT INTO trigger_destinations (id, name, description) "
            "VALUES (:id, :name, :description) "
            "ON CONFLICT (id) DO NOTHING"
        ),
        {
            "id": "slack",
            "name": "Slack",
            "description": "Posted to one of your Slack channels via an incoming webhook.",
        },
    )


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(sa.text("DELETE FROM trigger_destinations WHERE id = 'slack'"))
    op.drop_column("triggers", "slack_webhook_id")
    op.drop_index("idx_slack_webhooks_owner", table_name="slack_webhooks")
    op.drop_table("slack_webhooks")
