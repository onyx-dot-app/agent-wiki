"""ingest_settings.api_key: bearer token for POST /api/documents/ingest

Revision ID: 0018
Revises: 0017
Create Date: 2026-05-13 00:00:00.000000+00:00
"""
from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    cols = {c["name"] for c in inspector.get_columns("ingest_settings")}
    if "api_key" in cols:
        return
    op.add_column(
        "ingest_settings",
        sa.Column("api_key", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("ingest_settings", "api_key")
