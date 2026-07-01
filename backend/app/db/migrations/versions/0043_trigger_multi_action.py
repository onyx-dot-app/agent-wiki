"""drop the trigger slack_webhook_id column

The Slack channel a trigger posts to now lives in the ``destination_configs``
registry, referenced from ``action_json`` by ``destination_config_id``. Folding
existing Slack channels into that registry and rewriting the trigger YAML is
done in application code (``app/triggers/reconcile.py``), because it touches the
wiki git repo and encrypted secrets. This migration only drops the column.

Guarded on the column existing: ``0001_initial`` builds a fresh schema from the
current models (which no longer declare it) while ``0023`` adds it on the
upgrade path, mirroring ``0023``'s own guard.

Revision ID: 0043
Revises: 0042
Create Date: 2026-07-01 00:00:00.000000+00:00
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0043"
down_revision: str | None = "0042"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _has_slack_webhook_id() -> bool:
    cols = {c["name"] for c in sa.inspect(op.get_bind()).get_columns("triggers")}
    return "slack_webhook_id" in cols


def upgrade() -> None:
    if _has_slack_webhook_id():
        op.drop_column("triggers", "slack_webhook_id")


def downgrade() -> None:
    if not _has_slack_webhook_id():
        op.add_column("triggers", sa.Column("slack_webhook_id", sa.Text, nullable=True))
