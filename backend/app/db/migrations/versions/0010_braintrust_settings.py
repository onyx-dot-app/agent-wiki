"""braintrust settings table

Adds the singleton ``braintrust_settings`` row that backs the
admin-configured Braintrust project / API key / enabled flag. The shape
is enforced by the pydantic model in ``app/tracing/settings.py``; the
column-existence guard mirrors the pattern in ``0009_user_settings``
because ``0001_initial`` runs ``Base.metadata.create_all`` against the
live model registry — fresh databases bootstrapped after this table was
added to ``models.py`` will already have it.

Revision ID: 0010
Revises: 0009
Create Date: 2026-05-10 00:00:00.000000+00:00
"""
from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "braintrust_settings" in inspector.get_table_names():
        return
    op.create_table(
        "braintrust_settings",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=False),
        sa.Column("project", sa.Text, nullable=False, server_default=sa.text("''")),
        sa.Column("api_key", sa.Text, nullable=False, server_default=sa.text("''")),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.text("false")),
        sa.Column(
            "updated_at",
            sa.Text,
            nullable=False,
            server_default=sa.text(
                "to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS')"
            ),
        ),
        sa.CheckConstraint("id = 1", name="braintrust_settings_singleton"),
    )


def downgrade() -> None:
    op.drop_table("braintrust_settings")
