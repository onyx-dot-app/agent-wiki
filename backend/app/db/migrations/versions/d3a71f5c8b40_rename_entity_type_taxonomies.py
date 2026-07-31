"""rename entity_taxonomies -> entity_type_taxonomies

The table holds a taxonomy OF ENTITY TYPES, but ``entity_taxonomies`` reads as a taxonomy of
entities — an entity registry, which is a different thing this codebase does not have. Renamed
while the window is open: the table was introduced in ``e1b7c3a95d24`` and has no rows anywhere
and exactly one consumer, so nothing points at the old name yet. Once a derivation records a
taxonomy, type names become keys that stored data refers to and a rename stops being free.

Revision ID: d3a71f5c8b40
Revises: e1b7c3a95d24
Create Date: 2026-07-31 21:05:00.000000+00:00
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d3a71f5c8b40"
down_revision: str | None = "e1b7c3a95d24"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD = "entity_taxonomies"
_NEW = "entity_type_taxonomies"


def upgrade() -> None:
    # Two shapes of database reach this point and only one has anything to rename. A database
    # built fresh by ``0001_initial`` gets the table from the current models, so it already has
    # the new name; an existing one carries the old name from e1b7c3a95d24.
    inspector = sa.inspect(op.get_bind())
    if inspector.has_table(_NEW) or not inspector.has_table(_OLD):
        return
    op.rename_table(_OLD, _NEW)
    # The partial unique index enforcing "at most one active" is renamed too, so the constraint
    # is still findable by the name the model declares.
    op.execute(f"ALTER INDEX IF EXISTS uq_{_OLD}_active RENAME TO uq_{_NEW}_active")


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if not inspector.has_table(_NEW):
        return
    op.execute(f"ALTER INDEX IF EXISTS uq_{_NEW}_active RENAME TO uq_{_OLD}_active")
    op.rename_table(_NEW, _OLD)
