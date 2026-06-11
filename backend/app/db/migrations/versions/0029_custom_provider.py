"""custom-provider

Revision ID: 0029
Revises: 0028
Create Date: 2026-06-10 22:15:00.000000+00:00
"""

from __future__ import annotations

from typing import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0029"
down_revision: str = "0028"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Guarded adds: the 0001 bootstrap creates the full current schema via
    # create_all, so fresh installs already have these columns.
    bind = op.get_bind()
    insp = sa.inspect(bind)
    cols = {c["name"] for c in insp.get_columns("llm_settings")}

    for name in ("custom_api_key", "custom_base_url", "custom_display_name"):
        if name not in cols:
            op.add_column(
                "llm_settings",
                sa.Column(name, sa.Text, nullable=False, server_default=sa.text("''")),
            )


def downgrade() -> None:
    op.drop_column("llm_settings", "custom_api_key")
    op.drop_column("llm_settings", "custom_base_url")
    op.drop_column("llm_settings", "custom_display_name")
