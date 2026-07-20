"""source ranges

Adds ``source_ranges``, the content-level map from a span of a wiki page to the
ingested document that produced it. One row per changed span of an ingest
commit, anchored like a comment (``doc_path`` + ``anchor_sha`` + offsets) so the
same remap keeps it accurate and a rewrite retires it. Guarded with the
inspector because ``0001_initial`` builds fresh databases from the current
models.

Revision ID: d8b2f1a05c93
Revises: b3e8f5a90c27
Create Date: 2026-07-17 00:00:00.000000+00:00
"""

from __future__ import annotations

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "d8b2f1a05c93"
down_revision: str | None = "b3e8f5a90c27"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if inspector.has_table("source_ranges"):
        return
    op.create_table(
        "source_ranges",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("provenance_id", sa.Integer(), nullable=False),
        sa.Column("doc_path", sa.Text(), nullable=False),
        sa.Column("anchor_sha", sa.Text(), nullable=False),
        sa.Column("start_offset", sa.Integer(), nullable=False),
        sa.Column("end_offset", sa.Integer(), nullable=False),
        sa.Column("quoted_text", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), server_default=sa.text("'live'"), nullable=False),
        sa.Column(
            "created_at",
            sa.Text(),
            server_default=sa.text("to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS')"),
            nullable=False,
        ),
        sa.CheckConstraint("status IN ('live', 'retired')", name="source_ranges_status_check"),
        sa.ForeignKeyConstraint(["provenance_id"], ["provenance_ledger.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_source_ranges_doc_path_status", "source_ranges", ["doc_path", "status"])
    op.create_index("idx_source_ranges_provenance_id", "source_ranges", ["provenance_id"])


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if inspector.has_table("source_ranges"):
        op.drop_table("source_ranges")
