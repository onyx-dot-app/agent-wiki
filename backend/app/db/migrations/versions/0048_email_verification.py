"""email destination verification: verified_at, tokens, catalog row

Adds ``verified_at`` to ``destination_configs`` and the
``email_verification_tokens`` table, and seeds the ``email`` row in the
``trigger_destinations`` catalog. Schema changes are guarded on the live
inspector because ``0001_initial`` builds fresh databases from the current
model registry; the catalog seed is idempotent and always runs.

Revision ID: 0048
Revises: 0047
Create Date: 2026-07-02 00:00:00.000000+00:00
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0048"
down_revision: str | None = "0047"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    cols = {c["name"] for c in inspector.get_columns("destination_configs")}
    if "verified_at" not in cols:
        op.add_column("destination_configs", sa.Column("verified_at", sa.Text, nullable=True))

    if "email_verification_tokens" not in inspector.get_table_names():
        op.create_table(
            "email_verification_tokens",
            sa.Column("token", sa.Text, primary_key=True),
            sa.Column(
                "destination_config_id",
                sa.Text,
                sa.ForeignKey("destination_configs.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("expires_at", sa.Text, nullable=False),
            sa.Column("consumed_at", sa.Text, nullable=True),
            sa.Column(
                "created_at",
                sa.Text,
                nullable=False,
                server_default=sa.text(
                    "to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS')"
                ),
            ),
        )

    bind.execute(
        sa.text(
            "INSERT INTO trigger_destinations (id, name, description) "
            "VALUES (:id, :name, :description) "
            "ON CONFLICT (id) DO NOTHING"
        ),
        {
            "id": "email",
            "name": "Email",
            "description": "Sent to a verified email address.",
        },
    )


def downgrade() -> None:
    op.drop_table("email_verification_tokens")
    op.drop_column("destination_configs", "verified_at")
    op.get_bind().execute(sa.text("DELETE FROM trigger_destinations WHERE id = 'email'"))
