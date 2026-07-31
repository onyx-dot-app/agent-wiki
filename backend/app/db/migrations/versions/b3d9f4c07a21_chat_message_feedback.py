"""Add record-only feedback to chat messages."""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "b3d9f4c07a21"
down_revision: str | None = "f2c9a41e7b06"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    cols = {c["name"] for c in inspector.get_columns("chat_messages")}
    if "feedback" not in cols:
        op.add_column("chat_messages", sa.Column("feedback", sa.Text()))

    constraints = {
        c["name"] for c in inspector.get_check_constraints("chat_messages")
    }
    if "chat_messages_feedback_check" not in constraints:
        op.create_check_constraint(
            "chat_messages_feedback_check",
            "chat_messages",
            "feedback IS NULL OR feedback IN ('up', 'down')",
        )


def downgrade() -> None:
    op.drop_constraint(
        "chat_messages_feedback_check", "chat_messages", type_="check"
    )
    op.drop_column("chat_messages", "feedback")
