"""aspect_states

Adds ``aspect_states`` — one row per aspect holding the aspect's current
state: what its member needs say right now, unified, plus the conflict flag a
multi-page unification can raise when the pages disagree (see the model
docstring). Scoped to the aspect and CASCADEd from it, so a map re-derivation
tears states down with the aspects they describe.

Guarded with the inspector because ``0001_initial`` builds fresh databases
from the current models.

Revision ID: d7e4b9a2c1f6
Revises: a4d92e1c7f38
Create Date: 2026-08-07 00:00:00.000000+00:00
"""
from __future__ import annotations

from typing import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "d7e4b9a2c1f6"
down_revision: str | None = "a4d92e1c7f38"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if inspector.has_table("aspect_states"):
        return
    op.create_table(
        "aspect_states",
        sa.Column(
            "aspect_id",
            sa.Integer(),
            sa.ForeignKey("aspects.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("conflict", sa.Boolean(), nullable=False, server_default=sa.text("FALSE")),
        sa.Column("conflict_note", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("model", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column(
            "updated_at",
            sa.Text(),
            nullable=False,
            server_default=sa.text(
                "to_char((now() AT TIME ZONE 'UTC'), 'YYYY-MM-DD HH24:MI:SS')"
            ),
        ),
    )


def downgrade() -> None:
    op.drop_table("aspect_states")
