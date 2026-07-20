"""ai_management_settings.schedule

Adds the recurring-sweep ``schedule`` column (off / daily / weekly) to the
Auto Organize settings singleton. Guarded with the inspector because
``0001_initial`` builds fresh databases from the current models, which already
include the column.

Revision ID: 5c4a9e1b7d38
Revises: 3f6b8d0a1c25
Create Date: 2026-07-20 00:00:00.000000+00:00
"""
from __future__ import annotations

from typing import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "5c4a9e1b7d38"
down_revision: str | Sequence[str] | None = "3f6b8d0a1c25"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    cols = {c["name"] for c in inspector.get_columns("ai_management_settings")}
    if "schedule" in cols:
        return
    op.add_column(
        "ai_management_settings",
        sa.Column(
            "schedule", sa.Text(), nullable=False, server_default=sa.text("'off'")
        ),
    )
    op.create_check_constraint(
        "ai_management_settings_schedule_check",
        "ai_management_settings",
        "schedule IN ('off', 'daily', 'weekly')",
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    cols = {c["name"] for c in inspector.get_columns("ai_management_settings")}
    if "schedule" not in cols:
        return
    op.drop_constraint(
        "ai_management_settings_schedule_check",
        "ai_management_settings",
        type_="check",
    )
    op.drop_column("ai_management_settings", "schedule")
