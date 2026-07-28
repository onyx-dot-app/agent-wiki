"""wiki_doc_ids.last_viewed_at — durable last-viewed timestamp per page.

Revision ID: c2e7a4d9f1b8
Revises: b8d3f6a1c9e7

Feeds staleness detection: a page is only *considered* stale when both its
last edit (git) and last view (this column) are old. Lives on the stable-id
row (not a separate table): it's a single attribute of the page — an
event-log of views would earn its own table — and the id survives moves
and trash/restore, so no lifecycle re-keying is needed.
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
    cols = {c["name"] for c in inspector.get_columns("wiki_doc_ids")}
    if "last_viewed_at" in cols:
        return
    op.add_column("wiki_doc_ids", sa.Column("last_viewed_at", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("wiki_doc_ids", "last_viewed_at")
