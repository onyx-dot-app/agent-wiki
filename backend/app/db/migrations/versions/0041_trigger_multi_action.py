"""trigger action list + drop slack_webhook_id column

Folds each trigger's single ``{message, destination}`` blob plus its
``slack_webhook_id`` column into the ``action_json`` actions list
(``{"actions": [{type, message, slack_webhook_id}]}``), then drops the
now-redundant column. The channel id lives inside the action.

Guarded on whether the column exists: a schema built fresh from the current
models never has it, an upgraded database does.

Revision ID: 0041
Revises: 0040
Create Date: 2026-07-01 00:00:00.000000+00:00
"""

from __future__ import annotations

import json
from typing import Any, Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "0041"
down_revision: str | None = "0040"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_triggers = sa.table(
    "triggers",
    sa.column("id", sa.Text),
    sa.column("action_json", sa.Text),
    sa.column("slack_webhook_id", sa.Text),
)


def _load(raw: object) -> dict[str, Any]:
    if not isinstance(raw, str) or not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def upgrade() -> None:
    bind = op.get_bind()
    columns = {c["name"] for c in sa.inspect(bind).get_columns("triggers")}
    if "slack_webhook_id" not in columns:
        return

    rows = bind.execute(
        sa.select(_triggers.c.id, _triggers.c.action_json, _triggers.c.slack_webhook_id)
    ).all()
    for row in rows:
        data = _load(row.action_json)
        if "actions" in data:
            continue
        new_json = json.dumps(
            {
                "actions": [
                    {
                        "type": data.get("destination"),
                        "message": data.get("message"),
                        "slack_webhook_id": row.slack_webhook_id,
                    }
                ]
            }
        )
        bind.execute(
            sa.update(_triggers).where(_triggers.c.id == row.id).values(action_json=new_json)
        )

    op.drop_column("triggers", "slack_webhook_id")


def downgrade() -> None:
    bind = op.get_bind()
    columns = {c["name"] for c in sa.inspect(bind).get_columns("triggers")}
    if "slack_webhook_id" not in columns:
        op.add_column("triggers", sa.Column("slack_webhook_id", sa.Text, nullable=True))

    rows = bind.execute(sa.select(_triggers.c.id, _triggers.c.action_json)).all()
    for row in rows:
        data = _load(row.action_json)
        actions = data.get("actions")
        first = actions[0] if isinstance(actions, list) and actions else {}
        old_json = json.dumps(
            {"message": first.get("message"), "destination": first.get("type")}
        )
        bind.execute(
            sa.update(_triggers)
            .where(_triggers.c.id == row.id)
            .values(action_json=old_json, slack_webhook_id=first.get("slack_webhook_id"))
        )
