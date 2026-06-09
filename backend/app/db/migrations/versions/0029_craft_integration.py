"""Craft integration: agent_sessions external fields, user_onyx_connections,
craft_connect_states, notifications, ingest_settings.onyx_base_url

Revision ID: 0029
Revises: 0028
Create Date: 2026-06-09 00:00:00.000000
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0029"
down_revision: str = "0028"
branch_labels = None
depends_on = None

_NOW = sa.text("to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS')")


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)

    session_cols = {c["name"] for c in insp.get_columns("agent_sessions")}
    if "external_session_id" not in session_cols:
        op.add_column("agent_sessions", sa.Column("external_session_id", sa.Text, nullable=True))
    if "external_url" not in session_cols:
        op.add_column("agent_sessions", sa.Column("external_url", sa.Text, nullable=True))
    if "failure_reason" not in session_cols:
        op.add_column("agent_sessions", sa.Column("failure_reason", sa.Text, nullable=True))

    ingest_cols = {c["name"] for c in insp.get_columns("ingest_settings")}
    if "onyx_base_url" not in ingest_cols:
        op.add_column("ingest_settings", sa.Column("onyx_base_url", sa.Text, nullable=True))

    if not insp.has_table("user_onyx_connections"):
        op.create_table(
            "user_onyx_connections",
            sa.Column(
                "user_id",
                sa.Text,
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                primary_key=True,
            ),
            sa.Column("onyx_pat", sa.LargeBinary, nullable=False),
            sa.Column("token_display", sa.Text, nullable=False),
            sa.Column("onyx_user_email", sa.Text, nullable=True),
            sa.Column("expires_at", sa.Text, nullable=True),
            sa.Column("onyx_base_url", sa.Text, nullable=False),
            sa.Column("created_at", sa.Text, nullable=False, server_default=_NOW),
            sa.Column("updated_at", sa.Text, nullable=False, server_default=_NOW),
        )

    if not insp.has_table("craft_connect_states"):
        op.create_table(
            "craft_connect_states",
            sa.Column("state", sa.Text, primary_key=True),
            sa.Column(
                "user_id",
                sa.Text,
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("code_verifier", sa.Text, nullable=False),
            sa.Column("return_to", sa.Text, nullable=True),
            sa.Column("expires_at", sa.Text, nullable=False),
            sa.Column("consumed_at", sa.Text, nullable=True),
        )

    if not insp.has_table("notifications"):
        op.create_table(
            "notifications",
            sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
            sa.Column(
                "user_id",
                sa.Text,
                sa.ForeignKey("users.id", ondelete="CASCADE"),
                nullable=False,
            ),
            sa.Column("notif_type", sa.Text, nullable=False),
            sa.Column("title", sa.Text, nullable=False),
            sa.Column("description", sa.Text, nullable=True),
            sa.Column("dismissed", sa.Boolean, nullable=False, server_default=sa.text("FALSE")),
            sa.Column("first_shown", sa.Text, nullable=False, server_default=_NOW),
            sa.Column("last_shown", sa.Text, nullable=False, server_default=_NOW),
            sa.Column("data", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
            sa.UniqueConstraint(
                "user_id", "notif_type", "data", name="uq_notifications_user_type_data"
            ),
        )
        op.create_index(
            "idx_notifications_user_dismissed",
            "notifications",
            ["user_id", "dismissed"],
        )


def downgrade() -> None:
    op.drop_table("notifications")
    op.drop_table("craft_connect_states")
    op.drop_table("user_onyx_connections")
    op.drop_column("ingest_settings", "onyx_base_url")
    op.drop_column("agent_sessions", "failure_reason")
    op.drop_column("agent_sessions", "external_url")
    op.drop_column("agent_sessions", "external_session_id")
