"""provenance ledger

Adds ``provenance_ledger``, the structured record of who produced each wiki
commit and, for ingestion, which source document it came from. Guarded with
the inspector because ``0001_initial`` builds fresh databases from the current
models.

Revision ID: c7a1f0e3b2d9
Revises: f1a2b3c4d5e6
Create Date: 2026-07-13 00:00:00.000000+00:00
"""

from __future__ import annotations

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "c7a1f0e3b2d9"
down_revision: str | None = "f1a2b3c4d5e6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if inspector.has_table("provenance_ledger"):
        return
    op.create_table(
        "provenance_ledger",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("commit_sha", sa.Text(), nullable=False),
        sa.Column("doc_path", sa.Text(), nullable=False),
        sa.Column("actor_kind", sa.Text(), nullable=False),
        sa.Column("user_id", sa.Text(), nullable=True),
        sa.Column("agent_name", sa.Text(), nullable=True),
        sa.Column("agent_session_id", sa.Text(), nullable=True),
        sa.Column("source_document_id", sa.Text(), nullable=True),
        sa.Column("source_type", sa.Text(), nullable=True),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("source_title", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.Text(),
            server_default=sa.text("to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS')"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "actor_kind IN ('human', 'agent', 'ingestion', 'system')",
            name="provenance_ledger_actor_kind_check",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["agent_session_id"], ["agent_sessions.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("commit_sha", "doc_path", name="uq_provenance_ledger_commit_path"),
    )
    op.create_index("idx_provenance_ledger_doc_path", "provenance_ledger", ["doc_path"])
    op.create_index("idx_provenance_ledger_user_id", "provenance_ledger", ["user_id"])
    op.create_index(
        "idx_provenance_ledger_source_document_id", "provenance_ledger", ["source_document_id"]
    )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if inspector.has_table("provenance_ledger"):
        op.drop_table("provenance_ledger")
