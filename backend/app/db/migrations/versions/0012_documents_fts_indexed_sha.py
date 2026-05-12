"""documents_fts: add indexed_sha — no-op (pg_textsearch removed)

pg_textsearch has been removed in favour of OpenSearch. The documents_fts
table no longer exists; this migration is a no-op for new installs.

Revision ID: 0012
Revises: 0011
Create Date: 2026-05-10 00:12:00.000000+00:00
"""
from __future__ import annotations

from typing import Sequence

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
