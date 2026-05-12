"""initial — tables and seed catalogs.

Revision ID: 0001
Revises:
Create Date: 2026-05-09

This is the bootstrap migration. Rather than hand-listing every
``op.create_table`` call (which would drift the moment the next dev
edits ``app/db/models.py``), it calls
``Base.metadata.create_all(connection)`` to materialize every model
declared at the time of execution. From the *next* migration forward
we use ``alembic revision --autogenerate`` to produce explicit
``op.alter_table`` / ``op.add_column`` diffs.

Side effects beyond ORM table creation:
* Seeded ``trigger_destinations`` rows (currently just ``event_log``).

All idempotent so re-running against a partially-set-up DB is safe.
"""
from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()

    # Materialize every ORM-declared table. Imported inside the function
    # so test fixtures that swap modules don't bind to a stale ``Base``.
    from app.db.models import Base
    Base.metadata.create_all(bind)

    # Seed the v0 trigger destination. ``"event_log"`` means "record the
    # fire to the events table and don't dispatch outbound" — the only
    # delivery mode implemented so far.
    bind.execute(
        sa.text(
            "INSERT INTO trigger_destinations (id, name, description) "
            "VALUES (:id, :name, :description) "
            "ON CONFLICT (id) DO NOTHING"
        ),
        {
            "id": "event_log",
            "name": "Event Log",
            "description": "Tracked in the event log; not sent externally anywhere.",
        },
    )


def downgrade() -> None:
    raise NotImplementedError(
        "0001 is the bootstrap migration; downgrading would drop the entire schema."
    )
