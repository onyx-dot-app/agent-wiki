"""agent_activity cleanup_msg_id

Adds the bigint column used to track the pgmq msg_id of the cleanup
task scheduled for each ``agent_activity`` row, so re-registration can
cancel the prior delayed message instead of leaking it as an orphan.

The ADD COLUMN is guarded by a column-existence check because
``0001_initial`` calls ``Base.metadata.create_all(bind)`` against
whatever models are registered when it runs — fresh databases that
seed the schema after this column was added to ``models.py`` will
already have the column. Same pattern as ``0005_chat_sessions``.

Revision ID: 0006
Revises: 0005
Create Date: 2026-05-10 00:06:50.332178+00:00
"""
from __future__ import annotations

from typing import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {c["name"] for c in inspector.get_columns("agent_activity")}
    if "cleanup_msg_id" not in columns:
        op.add_column(
            "agent_activity",
            sa.Column("cleanup_msg_id", sa.BigInteger(), nullable=True),
        )


def downgrade() -> None:
    op.drop_column("agent_activity", "cleanup_msg_id")
