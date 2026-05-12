"""document_templates.sort_order: admin-controlled picker ordering

Adds an integer ``sort_order`` column so admins can drag templates into
the order users see in the new-doc picker. Lower values render first;
ties fall back to ``name`` alphabetical. Existing rows are backfilled
0..N-1 by alphabetical name so the first boot after this migration
keeps the order users have already been seeing.

Revision ID: 0017
Revises: 0016
Create Date: 2026-05-12 00:00:00.000000+00:00
"""
from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    cols = {c["name"] for c in inspector.get_columns("document_templates")}
    if "sort_order" in cols:
        return

    op.add_column(
        "document_templates",
        sa.Column(
            "sort_order",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    # Backfill: 0..N-1 in current alphabetical order so the picker
    # ordering after migration matches what users were seeing before.
    bind.execute(
        sa.text(
            """
            WITH ranked AS (
                SELECT id, ROW_NUMBER() OVER (ORDER BY name ASC) - 1 AS rn
                FROM document_templates
            )
            UPDATE document_templates t
            SET sort_order = ranked.rn
            FROM ranked
            WHERE t.id = ranked.id
            """
        )
    )


def downgrade() -> None:
    op.drop_column("document_templates", "sort_order")
