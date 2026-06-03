"""drop the vestigial ``documents`` table

The ``documents`` table (a metadata mirror of wiki page paths) had no readers
or writers in application code — page listing builds from ``git ls-files``, not
this table — so it was dead weight. Drop it.

Guarded on the live inspector: ``0001_initial`` runs
``Base.metadata.create_all`` against the *current* model registry, and the
``Document`` model has been removed, so freshly-bootstrapped databases never
create the table in the first place. Only existing databases carry it.

Revision ID: 0024
Revises: 0023
Create Date: 2026-06-03 00:00:00.000000+00:00
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
