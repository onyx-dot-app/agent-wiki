"""template ai management

Adds ``document_templates.ai_management_allowed`` — a template can opt pages
created from it into AI auto-management from birth (applied to the page's
update-policy row by ``apply_policy_to_page``). Starter templates ship with it
unset; admins opt specific templates in. Guarded with the inspector because
``0001_initial`` builds fresh databases from the current models.

Revision ID: e4b8c61d9a73
Revises: d4f8a26e9c11
Create Date: 2026-07-10 00:00:00.000000+00:00
"""
from __future__ import annotations

from typing import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "e4b8c61d9a73"
down_revision: str | None = "d4f8a26e9c11"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    cols = {c["name"] for c in inspector.get_columns("document_templates")}
    if "ai_management_allowed" not in cols:
        op.add_column(
            "document_templates",
            sa.Column("ai_management_allowed", sa.Boolean(), nullable=True),
        )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    cols = {c["name"] for c in inspector.get_columns("document_templates")}
    if "ai_management_allowed" in cols:
        op.drop_column("document_templates", "ai_management_allowed")
