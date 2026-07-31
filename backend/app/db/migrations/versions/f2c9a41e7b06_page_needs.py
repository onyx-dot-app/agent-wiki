"""page_needs

Adds ``page_needs``: per-page information needs, keyed by ``wiki_doc_ids.id``.

Keyed by doc id rather than path because extraction costs an LLM call per page and a move
re-keys the doc-id row in place — so a rename keeps its needs instead of looking like a new page
to buy and an old path to prune. No ``path`` column: after a move the row is intentionally not
stale, so a denormalized path would never be refreshed.

Current-valued rather than append-only (unlike ``entity_type_taxonomies``) because a page's needs
describe that page as it is now, and nothing keys facts by a need — so a re-extraction has
nothing to orphan. ``entity_type_taxonomy_id`` is ON DELETE SET NULL: losing a taxonomy must cost the
ability to resolve type names, not the needs themselves.

Revision ID: f2c9a41e7b06
Revises: d3a71f5c8b40
Create Date: 2026-07-31 14:20:00.000000+00:00
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "f2c9a41e7b06"
down_revision: str | None = "d3a71f5c8b40"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Guarded because ``0001_initial`` builds fresh databases from the current models, which
    # already carry this table.
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if inspector.has_table("page_needs"):
        return
    op.create_table(
        "page_needs",
        sa.Column("doc_id", sa.Text(), nullable=False),
        sa.Column("content_sha256", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("entity_type_taxonomy_id", sa.Integer(), nullable=True),
        sa.Column("needs", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "updated_at",
            sa.Text(),
            server_default=sa.text("to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS')"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["entity_type_taxonomy_id"], ["entity_type_taxonomies.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["doc_id"], ["wiki_doc_ids.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("doc_id"),
    )


def downgrade() -> None:
    op.drop_table("page_needs")
