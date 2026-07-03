"""coedit_ops client_id

Adds a nullable per-connection client id to the co-edit op log, so a
collaborative client can distinguish its own echoed op from a peer's.

Revision ID: 0050
Revises: 0049
Create Date: 2026-07-03
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0050"
down_revision: str | None = "0049"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Guarded: the bootstrap 0001 migration builds the full current schema via
    # create_all (already including this column on a fresh DB), so only add it
    # where an older DB lacks it.
    bind = op.get_bind()
    cols = {c["name"] for c in sa.inspect(bind).get_columns("coedit_ops")}
    if "client_id" not in cols:
        op.add_column("coedit_ops", sa.Column("client_id", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("coedit_ops", "client_id")
