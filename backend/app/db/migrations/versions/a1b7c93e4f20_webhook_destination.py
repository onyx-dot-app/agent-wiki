"""Seed the webhook trigger destination.

Registers the generic webhook destination catalog row so
``destination_configs`` of type ``webhook`` validate. The config's URL,
headers, and routing tag live in ``config_json`` and its signing secret in
the existing encrypted ``secret`` column, so no column changes are needed.

Revision ID: a1b7c93e4f20
Revises: 9d41b7c5e8a0
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import insert as pg_insert

revision: str = "a1b7c93e4f20"
down_revision: str | None = "9d41b7c5e8a0"
branch_labels = None
depends_on = None

# Core table construct for the catalog seed. Migrations do not import the live
# ORM model, so a lightweight sa.table expresses the seed without raw SQL.
_trigger_destinations = sa.table(
    "trigger_destinations",
    sa.column("id", sa.Text),
    sa.column("name", sa.Text),
    sa.column("description", sa.Text),
)


def upgrade() -> None:
    # on_conflict_do_nothing keeps re-application a no-op.
    op.get_bind().execute(
        pg_insert(_trigger_destinations)
        .values(
            id="webhook",
            name="Webhook",
            description="POST a structured event to an HTTP endpoint.",
        )
        .on_conflict_do_nothing(index_elements=["id"])
    )


def downgrade() -> None:
    op.get_bind().execute(
        _trigger_destinations.delete().where(_trigger_destinations.c.id == "webhook")
    )
