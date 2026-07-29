"""update_policies.ai_edits_disabled — the master AI Auto-Edits switch.

Revision ID: d7f2c8b4a1e6
Revises: c2e7a4d9f1b8

Tri-state, cascaded like the sibling fields. When effectively disabled it
overrides both sub-settings (ingestion auto-update, auto-management) at
resolution time without changing their stored values, so re-enabling the
master restores the children as they were.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "d7f2c8b4a1e6"
down_revision: str | None = "c2e7a4d9f1b8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    cols = {c["name"] for c in inspector.get_columns("update_policies")}
    if "ai_edits_disabled" in cols:
        return
    op.add_column(
        "update_policies", sa.Column("ai_edits_disabled", sa.Boolean(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("update_policies", "ai_edits_disabled")
