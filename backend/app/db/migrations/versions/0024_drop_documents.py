"""drop the ``documents`` table

Guarded on the live inspector: ``0001_initial`` runs
``Base.metadata.create_all`` against the current model registry, and the
``Document`` model is gone, so freshly-bootstrapped databases never create the
table — the guard makes the drop a no-op there; only existing databases carry
it. Downgrade recreates it (also guarded).

Revision ID: 0024
Revises: 0023
"""
from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0024"
down_revision: str | None = "0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "documents" in inspector.get_table_names():
        op.drop_table("documents")


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "documents" in inspector.get_table_names():
        return
    op.create_table(
        "documents",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column("path", sa.Text, nullable=False, unique=True),
        sa.Column("title", sa.Text),
        sa.Column(
            "updated_at",
            sa.Text,
            nullable=False,
            server_default=sa.text("to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS')"),
        ),
    )
