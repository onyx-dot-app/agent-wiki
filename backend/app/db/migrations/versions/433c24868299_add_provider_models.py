"""add-provider-models

Revision ID: 433c24868299
Revises: 0013
Create Date: 2026-05-11 19:00:28.267512+00:00
"""
from __future__ import annotations

from typing import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = '433c24868299'
down_revision: str | None = '0013'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(sa.text("ALTER TABLE llm_settings ADD COLUMN IF NOT EXISTS provider_models JSONB DEFAULT '{}'::jsonb NOT NULL"))


def downgrade() -> None:
    op.drop_column('llm_settings', 'provider_models')
