"""entity_type_taxonomies rename + page_needs

Two changes in one revision because they are one change to operators and neither has shipped:
``entity_taxonomies`` is renamed, and ``page_needs`` is created keying into it. Split across two
revisions, the rename would have to land first anyway — a ``page_needs`` created before it would
point its foreign key at a name about to disappear.

**Rename.** The table holds a taxonomy OF ENTITY TYPES, but ``entity_taxonomies`` reads as a
taxonomy of entities — an entity registry, which is a different thing this codebase does not
have. Renamed while the window is open: the table landed in ``e1b7c3a95d24``, has no rows in any
environment, and has one consumer, which arrives in this same revision. Once a derivation records
a taxonomy, its type NAMES become keys that stored needs refer to, and a rename stops being free.

**page_needs.** Per-page information needs, keyed by ``wiki_doc_ids.id`` rather than path because
extraction costs an LLM call per page and a move re-keys the doc-id row in place — so a rename
keeps its needs instead of looking like a new page to buy and an old path to prune. No ``path``
column: after a move the row is intentionally not stale, so a denormalized path would never be
refreshed. Current-valued rather than append-only (unlike ``entity_type_taxonomies``) because a
page's needs describe that page as it is now, and nothing keys facts by a need — so a
re-extraction has nothing to orphan. ``entity_type_taxonomy_id`` is ON DELETE SET NULL: losing a
taxonomy must cost the ability to resolve type names, not the needs themselves.

Revision ID: f2c9a41e7b06
Revises: e1b7c3a95d24
Create Date: 2026-07-31 14:20:00.000000+00:00
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "f2c9a41e7b06"
down_revision: str | None = "e1b7c3a95d24"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD = "entity_taxonomies"
_NEW = "entity_type_taxonomies"


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())

    # Two shapes of database reach this point and only one has anything to rename. A database
    # built fresh by ``0001_initial`` gets the table from the current models, so it already has
    # the new name; a deployed one carries the old name from e1b7c3a95d24.
    if inspector.has_table(_OLD) and not inspector.has_table(_NEW):
        op.rename_table(_OLD, _NEW)
        # Postgres renames NEITHER indexes nor constraints with the table, so each is renamed
        # explicitly. Without this an upgraded database and a database built fresh from the
        # models end up with different schemas — the fresh one gets
        # ``entity_type_taxonomies_pkey``, the upgraded one keeps ``entity_taxonomies_pkey`` —
        # and any later migration that names a constraint would then work on one and fail on the
        # other. IF EXISTS on each, because a database that reached this revision by some other
        # route may already carry the new names.
        op.execute(f"ALTER INDEX IF EXISTS uq_{_OLD}_active RENAME TO uq_{_NEW}_active")
        op.execute(f"ALTER INDEX IF EXISTS {_OLD}_pkey RENAME TO {_NEW}_pkey")
        op.execute(
            f"ALTER TABLE {_NEW} RENAME CONSTRAINT {_OLD}_triggered_by_fkey "
            f"TO {_NEW}_triggered_by_fkey"
        )

    # Guarded because ``0001_initial`` builds fresh databases from the current models, which
    # already carry this table.
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
        sa.ForeignKeyConstraint(["entity_type_taxonomy_id"], [f"{_NEW}.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["doc_id"], ["wiki_doc_ids.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("doc_id"),
    )


def downgrade() -> None:
    op.drop_table("page_needs")
    inspector = sa.inspect(op.get_bind())
    if inspector.has_table(_NEW) and not inspector.has_table(_OLD):
        op.execute(
            f"ALTER TABLE {_NEW} RENAME CONSTRAINT {_NEW}_triggered_by_fkey "
            f"TO {_OLD}_triggered_by_fkey"
        )
        op.execute(f"ALTER INDEX IF EXISTS {_NEW}_pkey RENAME TO {_OLD}_pkey")
        op.execute(f"ALTER INDEX IF EXISTS uq_{_NEW}_active RENAME TO uq_{_OLD}_active")
        op.rename_table(_NEW, _OLD)
