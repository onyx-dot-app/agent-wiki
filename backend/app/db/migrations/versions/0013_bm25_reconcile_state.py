"""bm25_reconcile_state: persist the reconcile sweep cursor

Single-row table holding ``last_completed_at`` for the hourly BM25
reconcile sweep. The scheduler's ``cron_state`` stamps ``last_fired_at``
*before* the task runs, so the task can't use it to learn when the
previous run finished — we need a separate cursor that only advances
on successful completion.

NULL means the sweep has never completed; the task interprets that as
"bootstrap" and scans the whole repo on first fire after deploy. After
that, each run looks only at paths whose git history touched them
since the cursor (with a small overlap).

Revision ID: 0013
Revises: 0012
Create Date: 2026-05-10 00:13:00.000000+00:00
"""
from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "bm25_reconcile_state" not in set(inspector.get_table_names()):
        op.create_table(
            "bm25_reconcile_state",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=False),
            sa.Column("last_completed_at", sa.Text(), nullable=True),
            sa.CheckConstraint("id = 1", name="bm25_reconcile_state_singleton"),
        )


def downgrade() -> None:
    op.drop_table("bm25_reconcile_state")
