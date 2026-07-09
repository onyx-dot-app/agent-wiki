"""change proposals

Adds ``change_proposals`` — the Wiki Auto Management proposal record
(op, paths, base SHAs, ACL fingerprints, lifecycle status). Inert at this
migration: no code emits or consumes rows yet. Guarded with the inspector
because ``0001_initial`` builds fresh databases from the current models.

Revision ID: c7d3e85f1b02
Revises: b5e2d19c7a44
Create Date: 2026-07-09 00:00:00.000000+00:00
"""
from __future__ import annotations

from typing import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "c7d3e85f1b02"
down_revision: str | None = "b5e2d19c7a44"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if inspector.has_table("change_proposals"):
        return
    op.create_table(
        "change_proposals",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("op", sa.Text(), nullable=False),
        sa.Column(
            "status", sa.Text(), nullable=False, server_default=sa.text("'pending'")
        ),
        sa.Column("source_paths", JSONB, nullable=False),
        sa.Column(
            "target_paths",
            JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "base_shas", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column("acl_fingerprint_before", sa.Text(), nullable=True),
        sa.Column("acl_fingerprint_after", sa.Text(), nullable=True),
        sa.Column("proposed_bodies", JSONB, nullable=True),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("instruction", sa.Text(), nullable=True),
        sa.Column("created_via", sa.Text(), nullable=False),
        sa.Column("run_id", sa.Text(), nullable=True),
        sa.Column(
            "acting_user_id",
            sa.Text(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "reviewed_by_user_id",
            sa.Text(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("status_reason", sa.Text(), nullable=True),
        sa.Column("applied_sha", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.Text(),
            nullable=False,
            server_default=sa.text("to_char((now() AT TIME ZONE 'UTC'), 'YYYY-MM-DD HH24:MI:SS')"),
        ),
        sa.Column(
            "updated_at",
            sa.Text(),
            nullable=False,
            server_default=sa.text("to_char((now() AT TIME ZONE 'UTC'), 'YYYY-MM-DD HH24:MI:SS')"),
        ),
        sa.Column("expires_at", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "op IN ('move', 'rename', 'merge', 'split', 'create_folder', "
            "'delete_empty_folder')",
            name="change_proposals_op_check",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'approved', 'applied', 'rejected', "
            "'expired', 'stale')",
            name="change_proposals_status_check",
        ),
        sa.CheckConstraint(
            "created_via IN ('sweep', 'on_create')",
            name="change_proposals_created_via_check",
        ),
    )
    op.create_index(
        "idx_change_proposals_status",
        "change_proposals",
        ["status", "created_at"],
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if inspector.has_table("change_proposals"):
        op.drop_table("change_proposals")
