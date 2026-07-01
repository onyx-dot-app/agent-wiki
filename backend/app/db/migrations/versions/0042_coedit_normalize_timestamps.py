"""coedit_sessions: normalize legacy space-separated timestamps to _iso

Sessions created before open_session started stamping created_at/updated_at in
_iso format carry the space-separated server-default (``YYYY-MM-DD HH:MM:SS``).
sessions_due_for_checkpoint compares those columns lexicographically against
_iso cutoffs (``YYYY-MM-DDTHH:MM:SS+00:00``); a space sorts before ``T``, so a
legacy row would look overdue immediately. Rewrite any such rows to _iso so the
string comparison is well-ordered.

Revision ID: 0042
Revises: 0041
Create Date: 2026-07-01 00:00:00.000000+00:00
"""
from __future__ import annotations

from typing import Sequence

from alembic import op


revision: str = "0042"
down_revision: str | None = "0041"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # A legacy value has no 'T' separator; convert 'YYYY-MM-DD HH:MM:SS' to
    # 'YYYY-MM-DDTHH:MM:SS+00:00'. Rows already in _iso (which contain 'T') are
    # skipped, so this is idempotent.
    for col in ("created_at", "updated_at", "last_checkpoint_at"):
        op.execute(
            f"UPDATE coedit_sessions "
            f"SET {col} = replace({col}, ' ', 'T') || '+00:00' "
            f"WHERE {col} IS NOT NULL AND position('T' in {col}) = 0"
        )


def downgrade() -> None:
    # No-op: _iso timestamps are a valid superset; nothing to roll back.
    pass
