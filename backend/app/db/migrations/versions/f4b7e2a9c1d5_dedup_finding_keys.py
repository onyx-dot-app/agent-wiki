"""finding_key + subject_key + doc_ids on change_proposals — dedup identity.

Revision ID: f4b7e2a9c1d5
Revises: d5e8a1c4f7b2

The dedup component (``app/wiki/automanage/dedup.py``) keys findings by
(detector, op, doc-id set, premise) and subjects by its content-free prefix.
Nullable: rows predating the columns keep working through the legacy
path-based dedupe-key guard. ``if_not_exists`` because ``0001_initial``
materializes model metadata (columns + indexes) on fresh installs.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "f4b7e2a9c1d5"
down_revision: str | None = "d5e8a1c4f7b2"
branch_labels: str | None = None
depends_on: str | None = None


def _has_column(table: str, column: str) -> bool:
    bind = op.get_bind()
    cols = sa.inspect(bind).get_columns(table)
    return any(c["name"] == column for c in cols)


def upgrade() -> None:
    if not _has_column("change_proposals", "finding_key"):
        op.add_column("change_proposals", sa.Column("finding_key", sa.Text()))
    if not _has_column("change_proposals", "doc_ids"):
        op.add_column(
            "change_proposals",
            sa.Column("doc_ids", postgresql.JSONB(astext_type=sa.Text())),
        )
    if not _has_column("change_proposals", "emit_count"):
        op.add_column(
            "change_proposals",
            sa.Column(
                "emit_count",
                sa.Integer(),
                nullable=False,
                server_default=sa.text("1"),
            ),
        )
    if not _has_column("change_proposals", "last_emitted_at"):
        op.add_column(
            "change_proposals", sa.Column("last_emitted_at", sa.Text())
        )
    if not _has_column("change_proposals", "subject_key"):
        op.add_column("change_proposals", sa.Column("subject_key", sa.Text()))
    op.create_index(
        "idx_change_proposals_finding_key",
        "change_proposals",
        ["finding_key"],
        if_not_exists=True,
    )
    op.create_index(
        "idx_change_proposals_subject_key",
        "change_proposals",
        ["subject_key"],
        if_not_exists=True,
    )


def downgrade() -> None:
    op.drop_index(
        "idx_change_proposals_finding_key",
        table_name="change_proposals",
        if_exists=True,
    )
    op.drop_index(
        "idx_change_proposals_subject_key",
        table_name="change_proposals",
        if_exists=True,
    )
    op.drop_column("change_proposals", "subject_key")
    op.drop_column("change_proposals", "finding_key")
    op.drop_column("change_proposals", "doc_ids")
    op.drop_column("change_proposals", "emit_count")
    op.drop_column("change_proposals", "last_emitted_at")
