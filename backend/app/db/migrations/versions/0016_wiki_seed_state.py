"""wiki_seed_state: one-shot marker for the bundled onboarding seed

Single-row table holding ``seeded_at`` for the lifespan's
``seed_if_empty`` hook. Once stamped, the seed will never run again on
this database — so if a user empties their wiki and reboots, the
onboarding pages do not come back uninvited.

NULL means the seed has never been considered on this DB. The hook
itself stamps the row the first time it runs (whether it wrote the
seed or observed pre-existing content), so existing instances pick up
the new marker on their next boot without a backfill.

Revision ID: 0016
Revises: 0015
Create Date: 2026-05-12 00:00:00.000000+00:00
"""
from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "wiki_seed_state" not in set(inspector.get_table_names()):
        op.create_table(
            "wiki_seed_state",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=False),
            sa.Column("seeded_at", sa.Text(), nullable=True),
            sa.CheckConstraint("id = 1", name="wiki_seed_state_singleton"),
        )


def downgrade() -> None:
    op.drop_table("wiki_seed_state")
