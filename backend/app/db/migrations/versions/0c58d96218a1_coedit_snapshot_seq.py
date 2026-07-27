"""coedit_snapshot_seq

Adds ``coedit_sessions.ydoc_snapshot_seq`` — the ``ydoc_seq`` the current
``ydoc_snapshot`` bytes represent. Pairs with the (already-existing)
``ydoc_snapshot`` column: a checkpoint rebuilds a throwaway ``Doc`` from the
snapshot plus every ``coedit_updates`` row with ``seq`` in
``(ydoc_snapshot_seq, ydoc_seq]``, rather than touching any process's live
in-memory room directly. Guarded with the inspector because ``0001_initial``
builds fresh databases from the current models.

Revision ID: 0c58d96218a1
Revises: 4a01439ee668
Create Date: 2026-07-27 22:23:31.926478+00:00
"""
from __future__ import annotations

from typing import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0c58d96218a1"
down_revision: str | None = "4a01439ee668"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def _has_column(table: str, column: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    return any(c["name"] == column for c in inspector.get_columns(table))


def upgrade() -> None:
    if not _has_column("coedit_sessions", "ydoc_snapshot_seq"):
        op.add_column(
            "coedit_sessions",
            sa.Column(
                "ydoc_snapshot_seq", sa.BigInteger(), server_default=sa.text("0"), nullable=False
            ),
        )


def downgrade() -> None:
    if _has_column("coedit_sessions", "ydoc_snapshot_seq"):
        op.drop_column("coedit_sessions", "ydoc_snapshot_seq")
