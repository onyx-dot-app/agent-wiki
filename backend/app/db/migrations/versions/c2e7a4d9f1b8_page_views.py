"""page_views — durable last-viewed timestamp per wiki page.

Revision ID: c2e7a4d9f1b8
Revises: b8d3f6a1c9e7

Feeds staleness detection: a page is only *considered* stale when both its
last edit (git) and last view (this table) are old. Path-keyed Postgres-only
metadata like update_policies; rows are re-keyed on moves and dropped on
deletes by the lifecycle seams.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "c2e7a4d9f1b8"
down_revision: str | None = "b8d3f6a1c9e7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if inspector.has_table("page_views"):
        return
    op.create_table(
        "page_views",
        sa.Column("path", sa.Text(), primary_key=True),
        sa.Column(
            "last_viewed_at",
            sa.Text(),
            nullable=False,
            server_default=sa.text(
                "to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS')"
            ),
        ),
    )


def downgrade() -> None:
    op.drop_table("page_views")
