"""user settings JSONB column

Adds the ``settings`` JSONB column on ``users`` that backs per-user
preferences (theme, timezone, default landing page, …). The shape is
enforced in app code via the ``UserSettings`` pydantic model
(``app/models/user_settings.py``); the column itself stores whatever
JSON the API persisted. New users default to ``{}`` and the read
path fills in defaults from the pydantic model, so no backfill is
needed for existing rows.

Guarded by a column-existence check because ``0001_initial`` runs
``Base.metadata.create_all`` against the live model registry —
fresh databases bootstrapped after this column was added to
``models.py`` will already have it. Same pattern as
``0008_trigger_schedule_columns``.

Revision ID: 0009
Revises: 0008
Create Date: 2026-05-10 00:00:00.000000+00:00
"""
from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {c["name"] for c in inspector.get_columns("users")}
    if "settings" not in columns:
        op.add_column(
            "users",
            sa.Column(
                "settings",
                postgresql.JSONB,
                nullable=False,
                server_default=sa.text("'{}'::jsonb"),
            ),
        )


def downgrade() -> None:
    op.drop_column("users", "settings")
