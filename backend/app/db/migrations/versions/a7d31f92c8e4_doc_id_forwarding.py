"""wiki_doc_ids.forwarded_to — retired ids resolve to the surviving document

Revision ID: a7d31f92c8e4
Revises: a9e2c7f4b1d6
Create Date: 2026-07-22
"""
from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a7d31f92c8e4"
down_revision: str | Sequence[str] | None = "a9e2c7f4b1d6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


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
