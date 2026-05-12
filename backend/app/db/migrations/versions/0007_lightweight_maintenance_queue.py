"""rename wiki_bm25 pgmq queue → lightweight_maintenance (no-op: pgmq removed)

pgmq has been removed in favour of Redis Streams. This migration is now a
no-op for new installs. Existing installs that still have the pgmq extension
will have their queue tables left in place; they can be cleaned up manually
or via a separate migration once the extension is dropped.

Revision ID: 0007
Revises: 0006
Create Date: 2026-05-10 00:08:00.000000+00:00
"""
from __future__ import annotations

from typing import Sequence

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
