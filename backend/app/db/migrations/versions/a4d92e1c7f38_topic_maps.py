"""topic map — runs, topics, aspects, and the pages holding them

The derived topic layer: subjects, their facets, and which pages hold each facet.

Relational rather than one JSONB document for two reasons that are not about size. An aspect can
belong to more than one topic — "implementation status" is a facet of several subjects — which
nesting cannot express; and a page reference can be a real foreign key, so a deleted page cannot
leave a dangling row.

Everything is scoped to a run and cascades from it, so a derivation is one insert and a prune is
one delete. One run is ``active``; the rest stay readable, because topic and aspect ids are only
stable WITHIN a run.

Revision ID: a4d92e1c7f38
Revises: f2c9a41e7b06
Create Date: 2026-08-04 20:40:00.000000+00:00
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a4d92e1c7f38"
down_revision: str | None = "f2c9a41e7b06"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NOW = sa.text("to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS')")


def upgrade() -> None:
    # Guarded because ``0001_initial`` builds fresh databases from the current models, which
    # already carry these tables.
    inspector = sa.inspect(op.get_bind())
    if inspector.has_table("topic_map_runs"):
        return

    op.create_table(
        "topic_map_runs",
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
        "uq_topic_map_runs_active",
        "topic_map_runs",
        ["active"],
        unique=True,
        postgresql_where=sa.text("active"),
    )

    op.create_table(
        "topics",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column(
            "subject_entity_type", sa.Text(), server_default=sa.text("''"), nullable=False
        ),
        sa.ForeignKeyConstraint(["run_id"], ["topic_map_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_topics_run_id", "topics", ["run_id"])

    op.create_table(
        "aspects",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("need_kind", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("detail_level", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("focus", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["topic_map_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_aspects_run_id", "aspects", ["run_id"])

    op.create_table(
        "topic_aspects",
        sa.Column("topic_id", sa.Integer(), nullable=False),
        sa.Column("aspect_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["topic_id"], ["topics.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["aspect_id"], ["aspects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("topic_id", "aspect_id"),
    )
    op.create_index("ix_topic_aspects_aspect_id", "topic_aspects", ["aspect_id"])

    # Natural key rather than a surrogate id: it says what the row is, and makes a duplicate
    # page-link impossible. ``entity`` is part of it because one page legitimately holds many
    # entities' rows for one aspect.
    op.create_table(
        "aspect_pages",
        sa.Column("aspect_id", sa.Integer(), nullable=False),
        sa.Column("doc_id", sa.Text(), nullable=False),
        sa.Column("entity", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("need_name", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.ForeignKeyConstraint(["aspect_id"], ["aspects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["doc_id"], ["wiki_doc_ids.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("aspect_id", "doc_id", "entity"),
    )
    # The reverse lookup a reconciler needs: given a page, which aspects does it hold? The aspect
    # side is already served by the primary key's leading column.
    op.create_index("ix_aspect_pages_doc_id", "aspect_pages", ["doc_id"])


def downgrade() -> None:
    op.drop_table("aspect_pages")
    op.drop_table("topic_aspects")
    op.drop_table("aspects")
    op.drop_table("topics")
    op.drop_index("uq_topic_map_runs_active", table_name="topic_map_runs")
    op.drop_table("topic_map_runs")
