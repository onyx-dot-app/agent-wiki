"""ai system user

Adds ``users.kind`` (``human`` | ``system``) and seeds the AI system user —
the principal that attributes autonomous wiki work (commit authorship, ACL
grants, page ownership) for Wiki Auto Management. Guarded with the inspector
because ``0001_initial`` builds fresh databases from the current models.

Revision ID: b5e2d19c7a44
Revises: a1b7c93e4f20
Create Date: 2026-07-09 00:00:00.000000+00:00
"""
from __future__ import annotations

from typing import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "b5e2d19c7a44"
down_revision: str | None = "a1b7c93e4f20"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Fixed, well-known id — code references the AI user by this constant
# (app/auth/users.py:AI_USER_ID), never by email lookup.
AI_USER_ID = "system-wiki-ai"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    cols = {c["name"] for c in inspector.get_columns("users")}
    if "kind" not in cols:
        op.add_column(
            "users",
            sa.Column(
                "kind", sa.Text(), nullable=False, server_default=sa.text("'human'")
            ),
        )
    # Seed the AI user. No password hash (can never log in), not admin, active.
    bind.execute(
        sa.text(
            """
            INSERT INTO users (id, email, name, password_hash, is_admin, is_active, kind)
            VALUES (:id, :email, :name, NULL, FALSE, TRUE, 'system')
            ON CONFLICT (id) DO NOTHING
            """
        ),
        {
            "id": AI_USER_ID,
            "email": "wiki-ai@system.local",
            "name": "Agent Wiki AI",
        },
    )


def downgrade() -> None:
    bind = op.get_bind()
    bind.execute(sa.text("DELETE FROM users WHERE id = :id"), {"id": AI_USER_ID})
    inspector = sa.inspect(bind)
    cols = {c["name"] for c in inspector.get_columns("users")}
    if "kind" in cols:
        op.drop_column("users", "kind")
