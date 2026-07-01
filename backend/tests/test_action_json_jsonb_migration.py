"""0044 migration: a text ``action_json`` column holding JSON strings converts
to jsonb in place.

Normal test schemas get the column as jsonb straight from ``0001_initial``'s
``create_all``, so the text->jsonb branch never runs there. This test
reproduces a pre-0044 database by rewinding the column to text and the alembic
stamp to 0043, then runs the real ``init_db()`` so 0044 actually converts.
"""
from __future__ import annotations

import sqlalchemy as sa

from app.db.session import init_db, session
from app.triggers import repo as triggers_repo

from tests._seed import seed_trigger, seed_user


def _rewind_to_text() -> None:
    with session() as s:
        s.execute(
            sa.text(
                "ALTER TABLE triggers ALTER COLUMN action_json "
                "TYPE text USING action_json::text"
            )
        )
        s.execute(sa.text("UPDATE alembic_version SET version_num = '0043'"))


def test_text_action_json_converts_to_jsonb(tmp_db: object) -> None:
    seed_user("usr_1")
    seed_trigger(
        tid="trg_1", owner_user_id="usr_1", scope_path="a.md", message="hi"
    )
    _rewind_to_text()

    init_db()

    with session() as s:
        col_type = s.execute(
            sa.text(
                "SELECT data_type FROM information_schema.columns "
                "WHERE table_schema = current_schema() "
                "AND table_name = 'triggers' AND column_name = 'action_json'"
            )
        ).scalar_one()
    assert col_type == "jsonb"

    t = triggers_repo.get("trg_1")
    assert t is not None
    assert t["actions"] == [{"destination_config_id": None, "message": "hi"}]
