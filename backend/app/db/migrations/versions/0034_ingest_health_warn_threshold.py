"""ingest auto-update health knobs + per-page warn_update_threshold

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


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # Fresh installs get these from 0001's create_all — guard so this is a no-op.
    policy_cols = {c["name"] for c in inspector.get_columns("update_policies")}
    if "warn_update_threshold" not in policy_cols:
        op.add_column(
            "update_policies",
            sa.Column("warn_update_threshold", sa.Integer(), nullable=True),
        )

    ingest_cols = {c["name"] for c in inspector.get_columns("ingest_settings")}
    if "warn_update_threshold_default" not in ingest_cols:
        op.add_column(
            "ingest_settings",
            sa.Column(
                "warn_update_threshold_default",
                sa.Integer(),
                server_default=sa.text("10"),
                nullable=False,
            ),
        )
    if "auto_update_cap" not in ingest_cols:
        op.add_column(
            "ingest_settings",
            sa.Column(
                "auto_update_cap",
                sa.Integer(),
                server_default=sa.text("200"),
                nullable=False,
            ),
        )


def downgrade() -> None:
    op.drop_column("ingest_settings", "auto_update_cap")
    op.drop_column("ingest_settings", "warn_update_threshold_default")
    op.drop_column("update_policies", "warn_update_threshold")
