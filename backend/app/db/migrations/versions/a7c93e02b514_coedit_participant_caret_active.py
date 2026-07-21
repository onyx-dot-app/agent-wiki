"""coedit_participant_caret_active

Adds ``coedit_participants.caret_active`` — whether the participant currently
has a caret placed in the text — and ``caret_seq``, the client-assigned caret
epoch that orders concurrent caret writes (guard: ``caret_seq < :seq``).
Presence renders roster members "editing" (positioned to edit) vs "viewing"
from the flag; the cursor endpoint sets/clears it on state transitions.
Guarded with the inspector because ``0001_initial`` builds fresh databases
from the current models.

Revision ID: a7c93e02b514
Revises: 5c4a9e1b7d38
Create Date: 2026-07-21 00:00:00.000000+00:00
"""
from __future__ import annotations

from typing import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "a7c93e02b514"
down_revision: str | None = "5c4a9e1b7d38"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def _has_column(table: str, column: str) -> bool:
    inspector = sa.inspect(op.get_bind())
    return any(c["name"] == column for c in inspector.get_columns(table))


def upgrade() -> None:
    if not _has_column("coedit_participants", "caret_active"):
        op.add_column(
            "coedit_participants",
            sa.Column(
                "caret_active",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("FALSE"),
            ),
        )
    if not _has_column("coedit_participants", "caret_seq"):
        op.add_column(
            "coedit_participants",
            sa.Column(
                "caret_seq",
                sa.BigInteger(),
                nullable=False,
                server_default=sa.text("0"),
            ),
        )


def downgrade() -> None:
    if _has_column("coedit_participants", "caret_seq"):
        op.drop_column("coedit_participants", "caret_seq")
    if _has_column("coedit_participants", "caret_active"):
        op.drop_column("coedit_participants", "caret_active")
