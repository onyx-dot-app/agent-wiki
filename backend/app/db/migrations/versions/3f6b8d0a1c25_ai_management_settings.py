"""ai_management_settings

Adds the org-wide AI-management (Auto Organize) settings singleton (id=1): the
master kill switch (``enabled``). Guarded with the inspector because
``0001_initial`` builds fresh databases from the current models.

Revision ID: 3f6b8d0a1c25
Revises: d8b2f1a05c93
Create Date: 2026-07-20 00:00:00.000000+00:00
"""
from __future__ import annotations

from typing import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "3f6b8d0a1c25"
down_revision: str | Sequence[str] | None = "d8b2f1a05c93"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if inspector.has_table("ai_management_settings"):
        return
    op.create_table(
        "ai_management_settings",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=False),
        sa.Column(
            "enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
        sa.Column(
            "updated_at",
            sa.Text(),
            nullable=False,
            server_default=sa.text(
                "to_char((now() AT TIME ZONE 'UTC'), 'YYYY-MM-DD HH24:MI:SS')"
            ),
        ),
        sa.Column(
            "updated_by_user_id",
            sa.Text(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.CheckConstraint("id = 1", name="ai_management_settings_singleton"),
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if inspector.has_table("ai_management_settings"):
        op.drop_table("ai_management_settings")
