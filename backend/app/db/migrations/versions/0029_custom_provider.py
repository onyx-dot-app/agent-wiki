"""custom-provider

Revision ID: 0029
Revises: 0028
Create Date: 2026-06-10 22:15:00.000000+00:00
"""

from __future__ import annotations

from typing import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0029"
down_revision: str = "0028"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        sa.text(
            "ALTER TABLE llm_settings ADD COLUMN IF NOT EXISTS custom_api_key TEXT NOT NULL DEFAULT ''"
        )
    )
    op.execute(
        sa.text(
            "ALTER TABLE llm_settings ADD COLUMN IF NOT EXISTS custom_base_url TEXT NOT NULL DEFAULT ''"
        )
    )
    op.execute(
        sa.text(
            "ALTER TABLE llm_settings ADD COLUMN IF NOT EXISTS custom_display_name TEXT NOT NULL DEFAULT ''"
        )
    )


def downgrade() -> None:
    op.drop_column("llm_settings", "custom_api_key")
    op.drop_column("llm_settings", "custom_base_url")
    op.drop_column("llm_settings", "custom_display_name")
