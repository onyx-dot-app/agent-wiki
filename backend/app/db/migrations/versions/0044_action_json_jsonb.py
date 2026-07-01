"""flip triggers.action_json from text to jsonb

The column holds the actions structure and every reader treats it as a dict,
so store it natively: no per-read parse, and it becomes queryable. Existing
text rows are valid JSON, so a plain ``::jsonb`` cast converts them.

Guarded on the live column type: a schema built fresh from the current models
is already jsonb, an upgraded database still has text.

Revision ID: 0044
Revises: 0043
Create Date: 2026-07-01 00:00:00.000000+00:00
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB


revision: str = "0044"
down_revision: str | None = "0043"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _column_type() -> str:
    cols = sa.inspect(op.get_bind()).get_columns("triggers")
    col = next((c for c in cols if c["name"] == "action_json"), None)
    if col is None:
        raise RuntimeError("triggers.action_json column not found")
    return str(col["type"]).lower()


def upgrade() -> None:
    if "json" not in _column_type():
        op.alter_column(
            "triggers",
            "action_json",
            existing_type=sa.Text(),
            type_=JSONB(),
            existing_nullable=False,
            postgresql_using="action_json::jsonb",
        )


def downgrade() -> None:
    if "json" in _column_type():
        op.alter_column(
            "triggers",
            "action_json",
            existing_type=JSONB(),
            type_=sa.Text(),
            existing_nullable=False,
            postgresql_using="action_json::text",
        )
