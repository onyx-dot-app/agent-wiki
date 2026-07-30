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
    # Guarded two ways: a fresh DB's 0001_initial builds the *current*
    # schema via create_all, which today has no coedit_ops table at all
    # (superseded by coedit_updates — see 4a01439ee668) — only a genuine
    # historical replay starting before 0040 ever creates it here to add
    # this column to. A test that rewinds the alembic stamp on an
    # already-at-head DB (no schema actually reverted, just the version
    # row) and re-runs from an earlier point hits exactly that case: the
    # table's long gone, so skip rather than let get_columns raise
    # NoSuchTableError.
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("coedit_ops"):
        return
    cols = {c["name"] for c in inspector.get_columns("coedit_ops")}
    if "client_id" not in cols:
        op.add_column("coedit_ops", sa.Column("client_id", sa.Text(), nullable=True))


def downgrade() -> None:
    bind = op.get_bind()
    if sa.inspect(bind).has_table("coedit_ops"):
        op.drop_column("coedit_ops", "client_id")
