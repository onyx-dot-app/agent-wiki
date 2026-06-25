"""document_templates: default update policy (auto-update + update instruction)

Revision ID: 0035
Revises: 0034
Create Date: 2026-06-25 00:00:00.000000+00:00
"""
from __future__ import annotations

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0035"
down_revision: str | None = "0034"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # Fresh installs get these from 0001's create_all — guard so this is a no-op.
    cols = {c["name"] for c in inspector.get_columns("document_templates")}
    if "ingestion_auto_update_disabled" not in cols:
        op.add_column(
            "document_templates",
            sa.Column("ingestion_auto_update_disabled", sa.Boolean(), nullable=True),
        )
    if "update_instruction" not in cols:
        op.add_column(
            "document_templates",
            sa.Column("update_instruction", sa.Text(), nullable=True),
        )


def downgrade() -> None:
    op.drop_column("document_templates", "update_instruction")
    op.drop_column("document_templates", "ingestion_auto_update_disabled")
