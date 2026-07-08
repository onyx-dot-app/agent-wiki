"""update policy ai management

Adds ``update_policies.ai_management_allowed`` — tri-state opt-in for AI
auto-management of a page/folder scope, resolved most-granular-wins like the
other cascaded policy fields. Guarded with the inspector because
``0001_initial`` builds fresh databases from the current models.

Revision ID: 9d41b7c5e8a0
Revises: 8c3fa27d9e42
Create Date: 2026-07-08 00:00:00.000000+00:00
"""
from __future__ import annotations

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "9d41b7c5e8a0"
down_revision: str | None = "8c3fa27d9e42"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    cols = {c["name"] for c in inspector.get_columns("update_policies")}
    if "ai_management_allowed" not in cols:
        op.add_column(
            "update_policies",
            sa.Column("ai_management_allowed", sa.Boolean(), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    cols = {c["name"] for c in inspector.get_columns("update_policies")}
    if "ai_management_allowed" in cols:
        op.drop_column("update_policies", "ai_management_allowed")
