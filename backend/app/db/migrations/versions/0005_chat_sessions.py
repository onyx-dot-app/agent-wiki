"""chat_sessions + chat_messages — persisted conversations for the ChatUI.

Revision ID: 0005
Revises: 0004
Create Date: 2026-05-09

The ``op.create_table`` calls are guarded by ``has_table`` because
``0001_initial`` calls ``Base.metadata.create_all(bind)`` against
whatever models are registered when 0001 runs — fresh databases will
already have these tables. The guard makes the migration a no-op there
and a real CREATE for databases that ran 0001 before this revision.
"""
from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_NOW_TEXT_DEFAULT = sa.text(
    "to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS')"
)


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not inspector.has_table("chat_sessions"):
        op.create_table(
            "chat_sessions",
            sa.Column("id", sa.Text(), primary_key=True),
            sa.Column(
                "user_id",
                sa.Text(),
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("title", sa.Text()),
            sa.Column(
                "created_at",
                sa.Text(),
                nullable=False,
                server_default=_NOW_TEXT_DEFAULT,
            ),
            sa.Column(
                "updated_at",
                sa.Text(),
                nullable=False,
                server_default=_NOW_TEXT_DEFAULT,
            ),
        )
        op.create_index(
            "idx_chat_sessions_user_updated",
            "chat_sessions",
            ["user_id", "updated_at"],
        )

    if not inspector.has_table("chat_messages"):
        op.create_table(
            "chat_messages",
            sa.Column("id", sa.Text(), primary_key=True),
            sa.Column(
                "session_id",
                sa.Text(),
                sa.ForeignKey("chat_sessions.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("ordering", sa.Integer(), nullable=False),
            sa.Column("role", sa.Text(), nullable=False),
            sa.Column(
                "content",
                sa.Text(),
                nullable=False,
                server_default=sa.text("''"),
            ),
            sa.Column("events_json", sa.Text()),
            sa.Column(
                "created_at",
                sa.Text(),
                nullable=False,
                server_default=_NOW_TEXT_DEFAULT,
            ),
            sa.CheckConstraint(
                "role IN ('user', 'assistant')",
                name="chat_messages_role_check",
            ),
        )
        op.create_index(
            "idx_chat_messages_session_order",
            "chat_messages",
            ["session_id", "ordering"],
        )


def downgrade() -> None:
    op.drop_index("idx_chat_messages_session_order", table_name="chat_messages")
    op.drop_table("chat_messages")
    op.drop_index("idx_chat_sessions_user_updated", table_name="chat_sessions")
    op.drop_table("chat_sessions")
