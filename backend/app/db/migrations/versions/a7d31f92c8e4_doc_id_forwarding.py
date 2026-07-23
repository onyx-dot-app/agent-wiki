"""wiki_doc_ids.forwarded_to — retired ids resolve to the surviving document

Revision ID: a7d31f92c8e4
Revises: 5c4a9e1b7d38
Create Date: 2026-07-22
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "a7d31f92c8e4"
down_revision = "5c4a9e1b7d38"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The bootstrap migration materializes current models via create_all, so a
    # fresh database already has this column — only pre-existing deployments
    # need the ALTER.
    bind = op.get_bind()
    cols = {c["name"] for c in sa.inspect(bind).get_columns("wiki_doc_ids")}
    if "forwarded_to" not in cols:
        op.add_column(
            "wiki_doc_ids", sa.Column("forwarded_to", sa.Text(), nullable=True)
        )


def downgrade() -> None:
    op.drop_column("wiki_doc_ids", "forwarded_to")
