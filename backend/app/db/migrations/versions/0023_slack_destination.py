"""slack webhook settings table + slack trigger destination

Adds the singleton ``slack_settings`` row that backs the admin-configured
Slack incoming webhook, and seeds a ``slack`` row into the
``trigger_destinations`` catalog so triggers can deliver fires to Slack.

The ``slack_settings`` create is guarded on ``inspector.get_table_names()``
because ``0001_initial`` runs ``Base.metadata.create_all`` against the live
model registry — fresh databases bootstrapped after this table was added to
``models.py`` will already have it (same pattern as ``0010_braintrust_settings``).
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
    if "slack_settings" not in inspector.get_table_names():
        op.create_table(
            "slack_settings",
            sa.Column("id", sa.Integer, primary_key=True, autoincrement=False),
            sa.Column(
                "webhook_url", sa.Text, nullable=False, server_default=sa.text("''")
            ),
            sa.Column(
                "enabled", sa.Boolean, nullable=False, server_default=sa.text("false")
            ),
            sa.Column(
                "updated_at",
                sa.Text,
                nullable=False,
                server_default=sa.text(
                    "to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS')"
                ),
            ),
            sa.CheckConstraint("id = 1", name="slack_settings_singleton"),
        )

    # Seed the slack destination row. Idempotent so re-running (or a fresh
    # DB whose 0001 already seeded only event_log) stays clean.
    bind.execute(
        sa.text(
            "INSERT INTO trigger_destinations (id, name, description) "
            "VALUES (:id, :name, :description) "
            "ON CONFLICT (id) DO NOTHING"
        ),
        {
            "id": "slack",
            "name": "Slack",
            "description": "Posted to a Slack channel via incoming webhook.",
        },
    )


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(sa.text("DELETE FROM trigger_destinations WHERE id = 'slack'"))
    op.drop_table("slack_settings")
