"""0047 migration: rows still in ``slack_webhooks`` are mirrored into
``destination_configs`` (encrypted secret moved verbatim, marker written)
before the table drops.

Normal test schemas never have the table (``0001_initial`` builds from the
current registry), so this reproduces a pre-0047 database by recreating the
table, seeding a row, and rewinding the alembic stamp before ``init_db()``.
"""
from __future__ import annotations

import sqlalchemy as sa

from app.db.crypto import encrypt_string
from app.db.session import init_db, session
from app.triggers import destination_configs as dest_configs

from tests._seed import seed_user

_HOOK = "https://hooks.slack.com/services/MIGRATED"


def _rewind_with_webhook_row() -> None:
    with session() as s:
        s.execute(
            sa.text(
                "CREATE TABLE slack_webhooks ("
                "id text PRIMARY KEY, "
                "owner_user_id text NOT NULL REFERENCES users(id) ON DELETE CASCADE, "
                "name text NOT NULL, "
                "webhook_url bytea NOT NULL, "
                "created_at text NOT NULL DEFAULT '2026-01-01 00:00:00')"
            )
        )
        s.execute(
            sa.text(
                "INSERT INTO slack_webhooks (id, owner_user_id, name, webhook_url) "
                "VALUES ('swh_deadbeef0001', 'usr_1', 'PM Standup', :blob)"
            ),
            {"blob": encrypt_string(_HOOK)},
        )
        s.execute(sa.text("UPDATE alembic_version SET version_num = '0046'"))


def test_unmirrored_webhook_row_migrates_before_drop(tmp_db):
    seed_user("usr_1")
    _rewind_with_webhook_row()

    init_db()

    configs = dest_configs.list_for_user("usr_1")
    assert len(configs) == 1
    cfg = configs[0]
    assert cfg["id"] == "dst_mdeadbeef0001"
    assert cfg["type"] == "slack"
    assert cfg["name"] == "PM Standup"
    assert cfg["config"] == {"from_slack_webhook": "swh_deadbeef0001"}
    assert dest_configs.get_secret(cfg["id"], owner_user_id="usr_1") == _HOOK

    with session() as s:
        tables = s.execute(
            sa.text(
                "SELECT count(*) FROM information_schema.tables "
                "WHERE table_schema = current_schema() AND table_name = 'slack_webhooks'"
            )
        ).scalar_one()
    assert tables == 0
