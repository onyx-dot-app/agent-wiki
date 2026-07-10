"""Merge the craft destination and template ai management heads.

#371 and #372 both chained from c7d3e85f1b02, leaving two heads and failing
``upgrade head`` at boot. This empty merge revision rejoins the tree so any
environment stamped at either branch (or their ancestor) upgrades cleanly.

Revision ID: 69b6ffd03a21
Revises: d4f8a26e9c11, e4b8c61d9a73
"""

from __future__ import annotations

from typing import Sequence

revision: str = "69b6ffd03a21"
down_revision: str | Sequence[str] | None = ("d4f8a26e9c11", "e4b8c61d9a73")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
