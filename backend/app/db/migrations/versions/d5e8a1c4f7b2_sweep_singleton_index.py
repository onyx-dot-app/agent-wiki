"""at most one running sweep — partial unique index on detection_runs.

Revision ID: d5e8a1c4f7b2
Revises: c9d4e7f2a1b8

Backs atomic sweep-slot acquisition (``automanage/runs.py:try_start_sweep``):
the ``running`` sweep row is the slot, so a guarded INSERT either takes it or
conflicts — no check-then-insert window. ``if_not_exists`` because
``0001_initial`` materializes the whole model metadata (index included) on
fresh databases.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "d5e8a1c4f7b2"
down_revision: str | None = "c9d4e7f2a1b8"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.create_index(
        "uq_detection_runs_single_running_sweep",
        "detection_runs",
        ["trigger"],
        unique=True,
        postgresql_where=sa.text("status = 'running' AND trigger = 'sweep'"),
        if_not_exists=True,
    )


def downgrade() -> None:
    op.drop_index(
        "uq_detection_runs_single_running_sweep",
        table_name="detection_runs",
        if_exists=True,
    )
