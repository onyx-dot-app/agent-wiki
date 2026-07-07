"""trigger multi-scope watch list

Adds ``triggers.scopes_json`` — the full watch list with optional line
ranges; ``scope_path`` keeps mirroring the first entry. Guarded with the
inspector because ``0001_initial`` builds fresh databases from the current
models.

Revision ID: 8c3fa27d9e42
Revises: 0075dcbb622e
Create Date: 2026-07-07 07:05:00.000000+00:00
"""
from __future__ import annotations

from typing import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB


revision: str = "8c3fa27d9e42"
down_revision: str | None = "0075dcbb622e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    cols = {c["name"] for c in inspector.get_columns("triggers")}
    if "scopes_json" not in cols:
        op.add_column("triggers", sa.Column("scopes_json", JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("triggers", "scopes_json")
