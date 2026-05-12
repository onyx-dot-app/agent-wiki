"""chat hidden columns

Adds ``hidden`` flags to chat_sessions and chat_messages. Sessions
created to bootstrap "drafting from template" conversations are marked
``hidden=TRUE`` so they don't appear in the user's session history list;
the synthetic seed user message that primes those conversations is also
``hidden=TRUE`` so the transcript looks like the agent kicked off on its
own (the LLM still sees it via the include_hidden=True hydration path).

Revision ID: 0015
Revises: 0014
Create Date: 2026-05-11 00:30:00.000000+00:00
"""
from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    sessions_cols = {c["name"] for c in inspector.get_columns("chat_sessions")}
    if "hidden" not in sessions_cols:
        op.add_column(
            "chat_sessions",
            sa.Column(
                "hidden",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("FALSE"),
            ),
        )

    messages_cols = {c["name"] for c in inspector.get_columns("chat_messages")}
    if "hidden" not in messages_cols:
        op.add_column(
            "chat_messages",
            sa.Column(
                "hidden",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("FALSE"),
            ),
        )


def downgrade() -> None:
    op.drop_column("chat_messages", "hidden")
    op.drop_column("chat_sessions", "hidden")
