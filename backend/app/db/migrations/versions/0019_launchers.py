"""coding-tool launchers — agent_sessions + launch_codes + page_working_dirs + launcher_tokens + agent_activity.agent_session_id.

Revision ID: 0019
Revises: 0018
Create Date: 2026-05-11

Phase 1 backend tables for the Run-Agent launcher. Three new tables for
the launch lifecycle, plus an encrypted-plaintext companion to
``mcp_tokens`` for tokens minted by the launcher (helper needs the raw
bearer; regular ``mcp_tokens`` stores only bcrypt hash).

``op.create_table`` calls are guarded by ``has_table`` because
``0001_initial`` runs ``Base.metadata.create_all`` and will materialize
these tables on fresh DBs authored after this revision shipped (same
pattern as ``0004_mcp_jobs.py``).
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0019"
down_revision: str | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_NOW_TEXT_DEFAULT = sa.text("to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS')")


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not inspector.has_table("agent_sessions"):
        op.create_table(
            "agent_sessions",
            sa.Column("id", sa.Text(), primary_key=True),
            sa.Column(
                "user_id",
                sa.Text(),
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("machine_id", sa.Text()),
            sa.Column("tool_id", sa.Text(), nullable=False),
            sa.Column("wiki_path", sa.Text()),
            sa.Column("working_dir", sa.Text()),
            sa.Column("first_turn_prompt", sa.Text(), nullable=False),
            sa.Column("cli_session_id", sa.Text()),
            sa.Column(
                "status",
                sa.Text(),
                nullable=False,
                server_default=sa.text("'pending'"),
            ),
            sa.Column("started_at", sa.Text(), nullable=False, server_default=_NOW_TEXT_DEFAULT),
            sa.Column(
                "last_activity_at",
                sa.Text(),
                nullable=False,
                server_default=_NOW_TEXT_DEFAULT,
            ),
            sa.Column("spawn_ok_at", sa.Text()),
            sa.Column("closed_at", sa.Text()),
        )
        op.create_index("idx_agent_sessions_user_status", "agent_sessions", ["user_id", "status"])
        op.create_index("idx_agent_sessions_wiki_path", "agent_sessions", ["wiki_path"])
        op.create_index(
            "idx_agent_sessions_user_machine",
            "agent_sessions",
            ["user_id", "machine_id"],
        )

    if not inspector.has_table("launch_codes"):
        op.create_table(
            "launch_codes",
            sa.Column("id", sa.Text(), primary_key=True),
            sa.Column(
                "user_id",
                sa.Text(),
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "agent_session_id",
                sa.Text(),
                sa.ForeignKey("agent_sessions.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "mcp_token_id",
                sa.Text(),
                sa.ForeignKey("mcp_tokens.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column(
                "created_at",
                sa.Text(),
                nullable=False,
                server_default=_NOW_TEXT_DEFAULT,
            ),
            sa.Column("expires_at", sa.Text(), nullable=False),
            sa.Column("consumed_at", sa.Text()),
        )
        op.create_index("idx_launch_codes_expires_at", "launch_codes", ["expires_at"])

    if not inspector.has_table("page_working_dirs"):
        op.create_table(
            "page_working_dirs",
            sa.Column(
                "user_id",
                sa.Text(),
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                primary_key=True,
            ),
            sa.Column("machine_id", sa.Text(), primary_key=True),
            sa.Column("wiki_path", sa.Text(), primary_key=True),
            sa.Column("working_dir", sa.Text(), nullable=False),
            sa.Column(
                "updated_at",
                sa.Text(),
                nullable=False,
                server_default=_NOW_TEXT_DEFAULT,
            ),
        )

    if not inspector.has_table("launcher_tokens"):
        op.create_table(
            "launcher_tokens",
            sa.Column(
                "mcp_token_id",
                sa.Text(),
                sa.ForeignKey("mcp_tokens.id", ondelete="CASCADE"),
                primary_key=True,
            ),
            sa.Column(
                "user_id",
                sa.Text(),
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                nullable=False,
                unique=True,
            ),
            sa.Column("ciphertext", sa.LargeBinary(), nullable=False),
            sa.Column("nonce", sa.LargeBinary(), nullable=False),
            sa.Column(
                "created_at",
                sa.Text(),
                nullable=False,
                server_default=_NOW_TEXT_DEFAULT,
            ),
        )

    # agent_activity.agent_session_id — additive nullable column.
    cols = {c["name"] for c in inspector.get_columns("agent_activity")}
    if "agent_session_id" not in cols:
        op.add_column(
            "agent_activity",
            sa.Column(
                "agent_session_id",
                sa.Text(),
                sa.ForeignKey("agent_sessions.id", ondelete="SET NULL"),
                nullable=True,
            ),
        )
        op.create_index("idx_agent_activity_session", "agent_activity", ["agent_session_id"])


def downgrade() -> None:
    op.drop_index("idx_agent_activity_session", table_name="agent_activity")
    op.drop_column("agent_activity", "agent_session_id")
    op.drop_table("launcher_tokens")
    op.drop_table("page_working_dirs")
    op.drop_index("idx_launch_codes_expires_at", table_name="launch_codes")
    op.drop_table("launch_codes")
    op.drop_index("idx_agent_sessions_user_machine", table_name="agent_sessions")
    op.drop_index("idx_agent_sessions_wiki_path", table_name="agent_sessions")
    op.drop_index("idx_agent_sessions_user_status", table_name="agent_sessions")
    op.drop_table("agent_sessions")
