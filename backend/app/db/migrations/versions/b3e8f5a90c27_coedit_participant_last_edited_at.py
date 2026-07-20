"""coedit_participant_last_edited_at

Adds ``coedit_participants.last_edited_at`` — when the participant last
applied an edit op (NULL for a participant who has only viewed). Presence
uses it to label roster members "viewing" vs "editing" now that joining a
session means opening the page, not editing it. Guarded with the inspector
because ``0001_initial`` builds fresh databases from the current models.

Revision ID: b3e8f5a90c27
Revises: f1a2b3c4d5e6
Create Date: 2026-07-20 00:00:00.000000+00:00
"""
from __future__ import annotations

from typing import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "b3e8f5a90c27"
down_revision: str | None = "f1a2b3c4d5e6"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def _has_column(table: str, column: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    return any(c["name"] == column for c in inspector.get_columns(table))


def upgrade() -> None:
    if not _has_column("coedit_participants", "last_edited_at"):
        op.add_column(
            "coedit_participants",
            sa.Column("last_edited_at", sa.Text(), nullable=True),
        )


def downgrade() -> None:
    if _has_column("coedit_participants", "last_edited_at"):
        op.drop_column("coedit_participants", "last_edited_at")
