"""coedit_ops.author_user_id nullable — allow system/merge-origin ops

Live-rebase folds an inbound agent commit into the buffer, and a checkpoint
syncs its AI-merged result back into the buffer; both are logged as ops with no
human author. Relax the NOT NULL so ``author_user_id IS NULL`` marks those.

Revision ID: 0043
Revises: 0042
Create Date: 2026-07-01 00:00:00.000000+00:00
"""
from __future__ import annotations

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0043"
down_revision: str | None = "0042"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    # Fresh installs get the nullable column from 0001's create_all — guard so
    # this only alters databases created before this change.
    col = {c["name"]: c for c in inspector.get_columns("coedit_ops")}.get("author_user_id")
    if col is not None and not col["nullable"]:
        op.alter_column("coedit_ops", "author_user_id", existing_type=sa.Text(), nullable=True)


def downgrade() -> None:
    op.alter_column("coedit_ops", "author_user_id", existing_type=sa.Text(), nullable=False)
