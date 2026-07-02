"""email_smtp_settings singleton for the app-wide outbound email account

Guarded on the live inspector because ``0001_initial`` builds fresh
databases from the current model registry.

Revision ID: 0049
Revises: 0048
Create Date: 2026-07-02 00:00:00.000000+00:00
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0049"
down_revision: str | None = "0048"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    if "email_smtp_settings" in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        "email_smtp_settings",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=False),
        sa.Column("host", sa.Text, nullable=False, server_default=sa.text("''")),
        sa.Column("port", sa.Integer, nullable=False, server_default=sa.text("587")),
        sa.Column("username", sa.Text, nullable=False, server_default=sa.text("''")),
        sa.Column("password", sa.LargeBinary, nullable=False),
        sa.Column("from_address", sa.Text, nullable=False, server_default=sa.text("''")),
        sa.Column(
            "updated_at",
            sa.Text,
            nullable=False,
            server_default=sa.text("to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS')"),
        ),
    )


def downgrade() -> None:
    op.drop_table("email_smtp_settings")
