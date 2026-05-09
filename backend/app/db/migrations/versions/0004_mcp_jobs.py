"""mcp_jobs — async job rows for the inbound MCP server's ``update_doc_nl``.

Revision ID: 0004
Revises: 0001
Create Date: 2026-05-09

Adds the ``mcp_jobs`` table backing
``local_data/wiki/mcp-server/mcp-server.md`` Phase 6. The partial unique
index on ``(user_id, idempotency_key)`` is the core idempotency
guarantee — a retried call with the same key returns the existing job
instead of enqueueing a duplicate LLM pass.

The ``op.create_table`` call is guarded by ``has_table`` because
``0001_initial`` calls ``Base.metadata.create_all(bind)``, which
materializes whatever is registered on ``Base.metadata`` at the moment
0001 runs — including this table, on fresh databases authored after
this revision shipped. The guard makes the migration a no-op in that
case (and a real CREATE for production databases that ran 0001 before
this revision existed).
"""
from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_NOW_TEXT_DEFAULT = sa.text(
    "to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS')"
)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not inspector.has_table("mcp_jobs"):
        op.create_table(
            "mcp_jobs",
            sa.Column("id", sa.Text(), primary_key=True),
            sa.Column(
                "user_id",
                sa.Text(),
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("kind", sa.Text(), nullable=False),
            sa.Column("status", sa.Text(), nullable=False),
            sa.Column("idempotency_key", sa.Text()),
            sa.Column("payload_json", sa.Text(), nullable=False),
            sa.Column("result_json", sa.Text()),
            sa.Column("error", sa.Text()),
            sa.Column(
                "created_at",
                sa.Text(),
                nullable=False,
                server_default=_NOW_TEXT_DEFAULT,
            ),
            sa.Column("finished_at", sa.Text()),
        )
        op.create_index(
            "idx_mcp_jobs_idemp",
            "mcp_jobs",
            ["user_id", "idempotency_key"],
            unique=True,
            postgresql_where=sa.text("idempotency_key IS NOT NULL"),
        )
        op.create_index("idx_mcp_jobs_user", "mcp_jobs", ["user_id"])


def downgrade() -> None:
    op.drop_index("idx_mcp_jobs_user", table_name="mcp_jobs")
    op.drop_index("idx_mcp_jobs_idemp", table_name="mcp_jobs")
    op.drop_table("mcp_jobs")
