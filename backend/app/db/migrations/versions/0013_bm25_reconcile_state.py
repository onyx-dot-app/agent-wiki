"""bm25_reconcile_state: no-op (pg_textsearch removed)

pg_textsearch and the BM25 reconcile sweep have been removed. This
migration is a no-op for new installs.

Revision ID: 0013
Revises: 0012
Create Date: 2026-05-10 00:13:00.000000+00:00
"""
from __future__ import annotations

from typing import Sequence

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
