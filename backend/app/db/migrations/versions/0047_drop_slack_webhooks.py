"""mirror remaining slack webhooks into destination_configs, then drop the table

Slack channels live in ``destination_configs``; the standalone webhook store
has no remaining readers. Rows not yet mirrored (an install that never booted
a mirror-era version) are copied here in SQL before the drop: the encrypted
secret is raw AES-GCM bytea with no column binding, so it moves verbatim, and
the mirror id derives from the webhook id so re-runs are idempotent.

Guarded on the live inspector because ``0001_initial`` builds fresh databases
from the current model registry, which no longer has the table.

Revision ID: 0047
Revises: 0046
Create Date: 2026-07-02 00:00:00.000000+00:00
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0047"
down_revision: str | None = "0046"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    if "slack_webhooks" not in sa.inspect(op.get_bind()).get_table_names():
        return
    # Copy any row without a mirror. dst_m<id-suffix> keys the mirror to its
    # source webhook (ids are swh_<12 hex>, so the derived id fits the dst_
    # shape), and the config marker matches what the boot reconcile resolves.
    op.execute(
        sa.text(
            """
            INSERT INTO destination_configs
                (id, owner_user_id, type, name, config_json, secret, created_at)
            SELECT 'dst_m' || substr(w.id, 5), w.owner_user_id, 'slack', w.name,
                   jsonb_build_object('from_slack_webhook', w.id),
                   w.webhook_url, w.created_at
            FROM slack_webhooks w
            WHERE NOT EXISTS (
                SELECT 1 FROM destination_configs d
                WHERE d.config_json->>'from_slack_webhook' = w.id
            )
            ON CONFLICT (id) DO NOTHING
            """
        )
    )
    op.drop_table("slack_webhooks")


def downgrade() -> None:
    # The table's data is not recoverable; recreate empty for schema parity.
    op.create_table(
        "slack_webhooks",
        sa.Column("id", sa.Text, primary_key=True),
        sa.Column(
            "owner_user_id",
            sa.Text,
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.Text, nullable=False),
        # Secret — AES-GCM encrypted at rest (app/db/crypto.py:EncryptedString).
        sa.Column("webhook_url", sa.LargeBinary, nullable=False),
        sa.Column(
            "created_at",
            sa.Text,
            nullable=False,
            server_default=sa.text("to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS')"),
        ),
    )
    op.create_index("idx_slack_webhooks_owner", "slack_webhooks", ["owner_user_id"])
