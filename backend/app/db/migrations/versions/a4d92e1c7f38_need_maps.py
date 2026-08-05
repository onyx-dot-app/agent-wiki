"""need map — one derivation of topics, aspects, and the needs composing them

The derived layer over page_needs: subjects, their facets, and which needs compose each
facet. Named for its input, not its output — a derivation groups the corpus's NEEDS, and the
topics and aspects are the names it invents for those groups.

Relational rather than one JSONB document because a page reference is a real foreign key: a
deleted page cannot leave a row pointing at nothing, which inside a blob would need filtering at
every read. Size is not the argument — at ten thousand pages the equivalent document is ~8.5 MB,
fine to cache but wrong to read per incoming document, while the reverse lookup stays indexed.

Everything is scoped to a map and cascades from it, so a derivation is one insert and a prune is
one delete. One map is ``active``; the rest stay readable, because topic and aspect ids are only
stable WITHIN a map.

State — the current value per aspect — is deliberately NOT here. It changes with every relevant
document while this is a snapshot, so it belongs in its own current-valued table.

Revision ID: a4d92e1c7f38
Revises: b3d9f4c07a21
Create Date: 2026-08-04 20:40:00.000000+00:00
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a4d92e1c7f38"
down_revision: str | None = "b3d9f4c07a21"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NOW = sa.text("to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS')")


def upgrade() -> None:
    # Guarded because ``0001_initial`` builds fresh databases from the current models, which
    # already carry these tables.
    inspector = sa.inspect(op.get_bind())
    if inspector.has_table("need_maps"):
        return

    op.create_table(
        "need_maps",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("active", sa.Boolean(), server_default=sa.text("FALSE"), nullable=False),
        sa.Column("corpus_fingerprint", sa.Text(), nullable=False),
        sa.Column("entity_type_taxonomy_id", sa.Integer(), nullable=True),
        sa.Column(
            "provenance",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "stats",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("triggered_by", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Text(), server_default=_NOW, nullable=False),
        sa.ForeignKeyConstraint(
            ["entity_type_taxonomy_id"], ["entity_type_taxonomies.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["triggered_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    # At most one active run, enforced by the database rather than by every writer remembering to
    # clear the previous one.
    op.create_index(
        "uq_need_maps_active",
        "need_maps",
        ["active"],
        unique=True,
        postgresql_where=sa.text("active"),
    )

    op.create_table(
        "topics",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("need_map_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.ForeignKeyConstraint(["need_map_id"], ["need_maps.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_topics_need_map_id", "topics", ["need_map_id"])

    op.create_table(
        "aspects",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("need_map_id", sa.Integer(), nullable=False),
        # One topic per aspect. A join table here would let a facet belong to several subjects,
        # which no producer emits and nothing downstream reads: reconciliation happens at the
        # aspect, which carries its own pages, so the topic is never consulted.
        sa.Column("topic_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), server_default=sa.text("''"), nullable=False),
        # No aspect_kind / detail_level / focus here: they differ between the pages holding a
        # facet and are authoritative on aspect_pages. A summary copy could drift from its rows,
        # and for free-text detail_level there is no summary to take. Computed on read instead.
        sa.ForeignKeyConstraint(["need_map_id"], ["need_maps.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["topic_id"], ["topics.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_aspects_need_map_id", "aspects", ["need_map_id"])
    op.create_index("ix_aspects_topic_id", "aspects", ["topic_id"])

    # Which needs make up an aspect — the connection, nothing else. A need has no id of its own
    # (a JSONB list on page_needs), so it is addressed by page + name, and that pair is the key:
    # the unit is the NEED, since clustering can put two of one page's needs in one aspect.
    #
    # No copy of the need's kind / detail_level / focus / entities. page_needs is current-valued
    # and this map is a snapshot, so a copy would drift as soon as a page was edited.
    op.create_table(
        "aspect_pages",
        sa.Column("aspect_id", sa.Integer(), nullable=False),
        sa.Column("doc_id", sa.Text(), nullable=False),
        sa.Column("need_name", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["aspect_id"], ["aspects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["doc_id"], ["wiki_doc_ids.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("aspect_id", "doc_id", "need_name"),
    )
    # The reverse lookup a reconciler needs: given a page, which aspects does it hold? The aspect
    # side is already served by the primary key's leading column.
    op.create_index("ix_aspect_pages_doc_id", "aspect_pages", ["doc_id"])


def downgrade() -> None:
    op.drop_table("aspect_pages")
    op.drop_table("aspects")
    op.drop_table("topics")
    op.drop_index("uq_need_maps_active", table_name="need_maps")
    op.drop_table("need_maps")
