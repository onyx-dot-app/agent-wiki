"""trigger schedule columns

Adds the three columns the schedule-kind trigger evaluator needs:

* ``schedule_timezone``    — IANA tz name the cron is evaluated in.
* ``schedule_start_at``    — optional UTC ISO 8601 "do not fire before" anchor.
* ``schedule_last_fired_at`` — UTC ISO 8601 of the most recent fire,
  written by the evaluator so croniter can advance on each tick.

Each ADD COLUMN is guarded by a column-existence check because
``0001_initial`` calls ``Base.metadata.create_all(bind)`` against
whatever models are registered when it runs — fresh databases that
seed the schema after these columns were added to ``models.py`` will
already have them. Same pattern as ``0006_agent_activity_cleanup_msg_id``.

Revision ID: 0008
Revises: 0007
Create Date: 2026-05-09 23:30:00.000000+00:00
"""
from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_NEW_COLUMNS = (
    "schedule_timezone",
    "schedule_start_at",
    "schedule_last_fired_at",
)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {c["name"] for c in inspector.get_columns("triggers")}
    for name in _NEW_COLUMNS:
        if name not in columns:
            op.add_column("triggers", sa.Column(name, sa.Text(), nullable=True))


def downgrade() -> None:
    for name in _NEW_COLUMNS:
        op.drop_column("triggers", name)
