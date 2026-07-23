"""provenance_ledger.source_snippet

Adds the ``source_snippet`` column: a leading slice of the pushed document's
content, captured at ingest for source previews. Guarded with the inspector
because ``0001_initial`` builds fresh databases from the current models,
which already include the column.

Revision ID: a9e2c7f4b1d6
Revises: 5c4a9e1b7d38
Create Date: 2026-07-22 00:00:00.000000+00:00
"""
from __future__ import annotations

from typing import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "a9e2c7f4b1d6"
down_revision: str | Sequence[str] | None = "5c4a9e1b7d38"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    cols = {c["name"] for c in inspector.get_columns("provenance_ledger")}
    if "source_snippet" in cols:
        return
    op.add_column(
        "provenance_ledger", sa.Column("source_snippet", sa.Text(), nullable=True)
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    cols = {c["name"] for c in inspector.get_columns("provenance_ledger")}
    if "source_snippet" not in cols:
        return
    op.drop_column("provenance_ledger", "source_snippet")
