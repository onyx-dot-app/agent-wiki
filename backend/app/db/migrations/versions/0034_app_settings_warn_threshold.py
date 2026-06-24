"""app settings + per-page warn_update_threshold

Revision ID: 0034
Revises: 0033
Create Date: 2026-06-24 00:00:00.000000+00:00
"""
from __future__ import annotations

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0034"
down_revision: str | None = "0033"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NOW = sa.text("to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS')")


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    # Fresh installs get both from 0001's create_all — guard so this is a no-op.
    cols = {c["name"] for c in inspector.get_columns("update_policies")}
    if "warn_update_threshold" not in cols:
        op.add_column(
            "update_policies",
            sa.Column("warn_update_threshold", sa.Integer(), nullable=True),
        )

    if "app_settings" not in tables:
        op.create_table(
            "app_settings",
            sa.Column("id", sa.Integer(), autoincrement=False, nullable=False),
            sa.Column(
                "warn_update_threshold_default",
                sa.Integer(),
                server_default=sa.text("10"),
                nullable=False,
            ),
            sa.Column(
                "auto_update_cap",
                sa.Integer(),
                server_default=sa.text("0"),
                nullable=False,
            ),
            sa.Column("updated_at", sa.Text(), server_default=_NOW, nullable=False),
            sa.CheckConstraint("id = 1", name="app_settings_singleton"),
            sa.PrimaryKeyConstraint("id"),
        )


def downgrade() -> None:
    op.drop_table("app_settings")
    op.drop_column("update_policies", "warn_update_threshold")
