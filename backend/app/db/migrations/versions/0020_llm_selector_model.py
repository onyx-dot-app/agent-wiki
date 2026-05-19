"""add ingest_selector_model to llm_settings

Revision ID: 0020
Revises: 433c24868299
Create Date: 2026-05-19 00:00:00.000000
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision: str = "0020"
down_revision: tuple[str, ...] = ("0019", "433c24868299")
branch_labels = None
depends_on = None


def upgrade() -> None:
    # IF NOT EXISTS is required because 0001_initial uses Base.metadata.create_all,
    # which already includes this column on fresh installs.
    op.execute(sa.text(
        "ALTER TABLE llm_settings ADD COLUMN IF NOT EXISTS"
        " ingest_selector_model TEXT NOT NULL DEFAULT ''"
    ))


def downgrade() -> None:
    op.drop_column("llm_settings", "ingest_selector_model")
