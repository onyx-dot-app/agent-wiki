"""change_proposals.detector — dispatch premise re-validation to the author

Revision ID: b3c8d4e5f6a7
Revises: a7d31f92c8e4
Create Date: 2026-07-23
"""
from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b3c8d4e5f6a7"
down_revision: str | Sequence[str] | None = "a7d31f92c8e4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # The bootstrap migration materializes current models via create_all, so a
    # fresh database already has this column — only pre-existing deployments
    # need the ALTER.
    bind = op.get_bind()
    cols = {c["name"] for c in sa.inspect(bind).get_columns("change_proposals")}
    if "detector" not in cols:
        op.add_column(
            "change_proposals", sa.Column("detector", sa.Text(), nullable=True)
        )


def downgrade() -> None:
    op.drop_column("change_proposals", "detector")
