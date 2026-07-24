"""Retire path-dedup-era pendings; make dedup_key unique.

Revision ID: b8d3f6a1c9e7
Revises: f4b7e2a9c1d5

Rows created before dedup_key existed can't be matched by the identity
dedup component; a pending one would get a keyed duplicate minted beside
it on the first post-deploy sweep. Stale them instead — the sweep
re-detects whatever is still true and re-creates it under identity keys
(stale is the designed retry path). Approved/applied/rejected rows are
left alone: approvals execute momentarily, and history is history.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "b8d3f6a1c9e7"
down_revision: str | None = "f4b7e2a9c1d5"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    # One finding = one row, structurally (partial: legacy NULL keys exempt).
    # Replaces the plain index from f4b7e2a9c1d5; if_not_exists because
    # 0001_initial materializes model metadata on fresh installs.
    op.drop_index(
        "idx_change_proposals_dedup_key",
        table_name="change_proposals",
        if_exists=True,
    )
    op.create_index(
        "ux_change_proposals_dedup_key",
        "change_proposals",
        ["dedup_key"],
        unique=True,
        postgresql_where=sa.text("dedup_key IS NOT NULL"),
        if_not_exists=True,
    )
    op.execute(
        sa.text(
            """
            UPDATE change_proposals
            SET status = 'stale',
                status_reason = 'superseded — re-detected under identity dedup keys',
                updated_at = to_char(now() AT TIME ZONE 'utc', 'YYYY-MM-DD HH24:MI:SS')
            WHERE status = 'pending' AND dedup_key IS NULL
            """
        )
    )


def downgrade() -> None:
    # The staled rows are recreated by sweeps (data-only forward fix).
    op.drop_index(
        "ux_change_proposals_dedup_key",
        table_name="change_proposals",
        if_exists=True,
    )
    op.create_index(
        "idx_change_proposals_dedup_key",
        "change_proposals",
        ["dedup_key"],
        if_not_exists=True,
    )
