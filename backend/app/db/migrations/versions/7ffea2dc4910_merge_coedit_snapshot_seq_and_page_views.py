"""merge coedit snapshot seq and page views

Revision ID: 7ffea2dc4910
Revises: 0c58d96218a1, c2e7a4d9f1b8
Create Date: 2026-07-28 16:41:16.390488+00:00
"""

from __future__ import annotations

from typing import Sequence


revision: str = "7ffea2dc4910"
down_revision: str | None = ("0c58d96218a1", "c2e7a4d9f1b8")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
