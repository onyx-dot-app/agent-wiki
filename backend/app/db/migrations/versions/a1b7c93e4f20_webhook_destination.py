"""Seed the webhook trigger destination.

Registers the generic webhook destination catalog row so
``destination_configs`` of type ``webhook`` validate. The config's URL,
headers, and routing tag live in ``config_json`` and its signing secret in
the existing encrypted ``secret`` column, so no column changes are needed.

Revision ID: a1b7c93e4f20
Revises: 8c3fa27d9e42
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "a1b7c93e4f20"
down_revision: str | None = "8c3fa27d9e42"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.get_bind().execute(
        sa.text(
            "INSERT INTO trigger_destinations (id, name, description) "
            "VALUES (:id, :name, :description) "
            "ON CONFLICT (id) DO NOTHING"
        ),
        {
            "id": "webhook",
            "name": "Webhook",
            "description": "POST a structured event to an HTTP endpoint.",
        },
    )


def downgrade() -> None:
    op.get_bind().execute(
        sa.text("DELETE FROM trigger_destinations WHERE id = 'webhook'")
    )
