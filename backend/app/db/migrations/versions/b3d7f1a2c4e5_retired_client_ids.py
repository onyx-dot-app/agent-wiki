"""retired client-id sets for replaced coedit lineages

Adds ``coedit_sessions.retired_client_ids`` and
``wiki_documents.retired_client_ids``: the Yjs client ids belonging to
lineages a reseed replaced, accumulated from the discarded document's state
vector. The foreign-state guard flags any connecting client whose state
vector contains one — a mixed document (old content plus current-lineage
broadcasts it kept integrating) shares ids with the current lineage, so an
overlap test alone cannot catch it; holding a retired id can only mean
retained replaced-lineage content.

Column adds are guarded on the live inspector because ``0001_initial`` runs
``Base.metadata.create_all`` against the current model registry.

Revision ID: b3d7f1a2c4e5
Revises: a9c2e5f8b3d1
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "b3d7f1a2c4e5"
down_revision: str | None = "a9c2e5f8b3d1"
branch_labels: str | None = None
depends_on: str | None = None


def _columns(inspector: sa.Inspector, table: str) -> set[str]:
    return {c["name"] for c in inspector.get_columns(table)}


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    for table in ("coedit_sessions", "wiki_documents"):
        if "retired_client_ids" not in _columns(inspector, table):
            op.add_column(
                table,
                sa.Column(
                    "retired_client_ids",
                    sa.dialects.postgresql.JSONB(),
                    nullable=False,
                    server_default=sa.text("'[]'::jsonb"),
                ),
            )


def downgrade() -> None:
    op.drop_column("wiki_documents", "retired_client_ids")
    op.drop_column("coedit_sessions", "retired_client_ids")
