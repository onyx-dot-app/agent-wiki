"""agent_activity: collapse to one row per (user, agent)

Replaces the natural key ``(user_id, agent_name, doc_path, activity)``
with the narrower ``(user_id, agent_name)`` so each (user, agent) has
exactly one row at a time. A newer upsert overwrites the older row in
place — the row now answers "what is this agent doing right now?"
rather than "every doc this agent has touched in the last 24h".

Existing rows are collapsed by keeping the most recent
``registered_at`` per (user, agent); the rest are deleted. Their
scheduled cleanup messages will fire and no-op via the
``expected_expires_at`` stale check.

Revision ID: 0011
Revises: 0010
Create Date: 2026-05-10 00:11:00.000000+00:00
"""
from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _existing_unique_names(bind: sa.Connection) -> set[str]:
    """Names of UNIQUE constraints currently on ``agent_activity``.

    ``0001_initial`` materializes the schema via ``Base.metadata.create_all``
    against the *current* models, so a fresh DB will already have the new
    constraint and not the old one. We branch on what's actually there.
    """
    inspector = sa.inspect(bind)
    return {uc["name"] for uc in inspector.get_unique_constraints("agent_activity")}


def upgrade() -> None:
    bind = op.get_bind()
    existing = _existing_unique_names(bind)

    # Collapse: keep only the most recent row per (user, agent).
    # `IS NOT DISTINCT FROM` makes NULL agent_name values match each other.
    op.execute(
        """
        DELETE FROM agent_activity a
        USING agent_activity b
        WHERE a.user_id = b.user_id
          AND a.agent_name IS NOT DISTINCT FROM b.agent_name
          AND (
              a.registered_at < b.registered_at
              OR (a.registered_at = b.registered_at AND a.id < b.id)
          )
        """
    )

    if "idx_agent_activity_natural_key" in existing:
        op.drop_constraint(
            "idx_agent_activity_natural_key", "agent_activity", type_="unique"
        )
    if "idx_agent_activity_user_agent" not in existing:
        op.create_unique_constraint(
            "idx_agent_activity_user_agent",
            "agent_activity",
            ["user_id", "agent_name"],
            postgresql_nulls_not_distinct=True,
        )


def downgrade() -> None:
    op.drop_constraint(
        "idx_agent_activity_user_agent", "agent_activity", type_="unique"
    )
    op.create_unique_constraint(
        "idx_agent_activity_natural_key",
        "agent_activity",
        ["user_id", "agent_name", "doc_path", "activity"],
        postgresql_nulls_not_distinct=True,
    )
