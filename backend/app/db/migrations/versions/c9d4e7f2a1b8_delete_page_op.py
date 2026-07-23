"""widen change_proposals op CHECK with delete_page

Revision ID: c9d4e7f2a1b8
Revises: b3c8d4e5f6a7
Create Date: 2026-07-23
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "c9d4e7f2a1b8"
down_revision: str | Sequence[str] | None = "b3c8d4e5f6a7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OPS = "'move', 'rename', 'merge', 'split', 'create_folder', 'delete_empty_folder'"


def upgrade() -> None:
    # Drop-and-recreate is idempotent against the bootstrap create_all (which
    # already materializes the widened constraint on fresh databases).
    op.drop_constraint("change_proposals_op_check", "change_proposals", type_="check")
    op.create_check_constraint(
        "change_proposals_op_check",
        "change_proposals",
        f"op IN ({_OPS}, 'delete_page')",
    )


def downgrade() -> None:
    op.drop_constraint("change_proposals_op_check", "change_proposals", type_="check")
    op.create_check_constraint(
        "change_proposals_op_check", "change_proposals", f"op IN ({_OPS})"
    )
