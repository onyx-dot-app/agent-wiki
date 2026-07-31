"""entity_taxonomies

Adds ``entity_taxonomies``: derived entity-type taxonomies, append-only with one row
flagged ``active``, plus ``ingest_settings.organization_name``. Append-only because entity types key facts by entity — a re-derivation
that renames a type must not orphan rows keyed under the old name, so the superseded
taxonomy has to stay readable rather than being overwritten.

Revision ID: e1b7c3a95d24
Revises: c8a4e7d2f6b1
Create Date: 2026-07-31 10:15:00.000000+00:00
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "e1b7c3a95d24"
down_revision: str | None = "c8a4e7d2f6b1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Guarded because ``0001_initial`` builds fresh databases from the current models,
    # which already carry this table.
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if inspector.has_table("entity_taxonomies"):
        return
    op.create_table(
        "entity_taxonomies",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("active", sa.Boolean(), server_default=sa.text("FALSE"), nullable=False),
        sa.Column("corpus_fingerprint", sa.Text(), nullable=False),
        sa.Column("types", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
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
        sa.Column(
            "created_at",
            sa.Text(),
            server_default=sa.text("to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS')"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["triggered_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    # Partial unique index: at most one active row, enforced by the database rather than by
    # every writer remembering to clear the previous one.
    op.create_index(
        "uq_entity_taxonomies_active",
        "entity_taxonomies",
        ["active"],
        unique=True,
        postgresql_where=sa.text("active"),
    )
    # The organisation the wiki belongs to, and who claimed it. A setting, not derived
    # output — see the model for why the source column gates inference rather than the value.
    existing = {c["name"] for c in inspector.get_columns("ingest_settings")}
    if "organization_name" not in existing:
        op.add_column("ingest_settings", sa.Column("organization_name", sa.Text(), nullable=True))
    if "organization_name_source" not in existing:
        op.add_column(
            "ingest_settings",
            sa.Column("organization_name_source", sa.Text(), nullable=True),
        )


def downgrade() -> None:
    op.drop_column("ingest_settings", "organization_name_source")
    op.drop_column("ingest_settings", "organization_name")
    op.drop_index("uq_entity_taxonomies_active", table_name="entity_taxonomies")
    op.drop_table("entity_taxonomies")
