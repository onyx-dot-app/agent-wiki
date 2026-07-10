"""Seed the craft trigger destination.

Registers the Craft destination catalog row so ``destination_configs`` of
type ``craft`` validate. The target is the owner's existing Onyx connection
(``user_onyx_connections``), so the config carries no URL and no secret and
no column changes are needed.

Revision ID: d4f8a26e9c11
Revises: c7d3e85f1b02
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import insert as pg_insert

revision: str = "d4f8a26e9c11"
down_revision: str | None = "c7d3e85f1b02"
branch_labels = None
depends_on = None

# Frozen lightweight table for the seed. The live ORM model drifts with
# later revisions, so the migration must not depend on it.
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
            id="craft",
            name="Onyx Craft",
            description="Start an Onyx Craft session seeded with the fire and the source page.",
        )
        .on_conflict_do_nothing(index_elements=["id"])
    )


def downgrade() -> None:
    op.get_bind().execute(
        _trigger_destinations.delete().where(_trigger_destinations.c.id == "craft")
    )
