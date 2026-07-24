"""image unreferenced_since

Adds ``images.unreferenced_since`` for wiki image retention state.
"""
from __future__ import annotations

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "e7a1c4d9b2f6"
down_revision: str | None = "c1d2e3f4a5b6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    cols = {c["name"] for c in sa.inspect(bind).get_columns("images")}
    if "unreferenced_since" not in cols:
        op.add_column("images", sa.Column("unreferenced_since", sa.Text(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    cols = {c["name"] for c in sa.inspect(bind).get_columns("images")}
    if "unreferenced_since" in cols:
        op.drop_column("images", "unreferenced_since")
