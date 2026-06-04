"""user is_active + updated_at, and the invited_users table

Revision ID: 0023
Revises: 0022
Create Date: 2026-06-02 00:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision: str = "0028"
down_revision: str = "0027"
branch_labels = None
depends_on = None

_NOW = sa.text("to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS')")


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    user_cols = {c["name"] for c in insp.get_columns("users")}
    if "is_active" not in user_cols:
        op.add_column(
            "users",
            sa.Column(
                "is_active",
                sa.Boolean,
                nullable=False,
                server_default=sa.text("TRUE"),
            ),
        )
    if "updated_at" not in user_cols:
        op.add_column(
            "users",
            sa.Column("updated_at", sa.Text, nullable=False, server_default=_NOW),
        )

    if not insp.has_table("invited_users"):
        op.create_table(
            "invited_users",
            sa.Column("email", sa.Text, primary_key=True),
            sa.Column(
                "invited_by_user_id",
                sa.Text,
                sa.ForeignKey("users.id", ondelete="SET NULL"),
            ),
            sa.Column("created_at", sa.Text, nullable=False, server_default=_NOW),
        )


def downgrade() -> None:
    op.drop_table("invited_users")
    op.drop_column("users", "updated_at")
    op.drop_column("users", "is_active")
