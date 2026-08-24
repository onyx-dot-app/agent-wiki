"""coedit lineage generation columns

Adds ``coedit_sessions.ydoc_lineage`` and ``coedit_updates.lineage``: the
session's CRDT lineage generation, bumped when the checkpoint engine reseeds
the document, and the generation each logged update was produced against.
Rebuilds replay only current-generation rows, and the WS layer rejects
updates from clients still on a replaced generation — an old-generation Yjs
update merged into a reseeded document unions both documents' content
(whole-page duplication) instead of conflicting.

Column adds are guarded on the live inspector because ``0001_initial`` runs
``Base.metadata.create_all`` against the current model registry — databases
bootstrapped after these columns joined ``models.py`` already have them.

Revision ID: a9c2e5f8b3d1
Revises: d7e4b9a2c1f6
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "a9c2e5f8b3d1"
down_revision: str | None = "d7e4b9a2c1f6"
branch_labels: str | None = None
depends_on: str | None = None


def _columns(inspector: sa.Inspector, table: str) -> set[str]:
    return {c["name"] for c in inspector.get_columns(table)}


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "ydoc_lineage" not in _columns(inspector, "coedit_sessions"):
        op.add_column(
            "coedit_sessions",
            sa.Column(
                "ydoc_lineage", sa.BigInteger(), nullable=False, server_default=sa.text("0")
            ),
        )
    if "lineage" not in _columns(inspector, "coedit_updates"):
        op.add_column(
            "coedit_updates",
            sa.Column(
                "lineage", sa.BigInteger(), nullable=False, server_default=sa.text("0")
            ),
        )
    if "ydoc_lineage" not in _columns(inspector, "wiki_documents"):
        op.add_column(
            "wiki_documents",
            sa.Column(
                "ydoc_lineage", sa.BigInteger(), nullable=False, server_default=sa.text("0")
            ),
        )


def downgrade() -> None:
    op.drop_column("wiki_documents", "ydoc_lineage")
    op.drop_column("coedit_updates", "lineage")
    op.drop_column("coedit_sessions", "ydoc_lineage")
