"""Tests for app/triggers/destination_configs.py — the per-user destination
config registry (the typed generalization of the Slack webhook registry).

Guards the owner-scoping and at-rest secret encryption that the trigger
dispatch path will rely on once destinations route through this table.
"""

from __future__ import annotations

import pytest

from app.triggers import destination_configs as configs

from tests._seed import seed_user

_SECRET = "https://hooks.slack.com/services/EXAMPLE"


def test_create_then_list(tmp_db):
    seed_user("usr_1")
    c = configs.create("usr_1", type="slack", name="PM Standup", secret=_SECRET)
    assert c["id"].startswith("dst_")
    assert c["type"] == "slack"
    assert c["has_secret"] is True

    rows = configs.list_for_user("usr_1")
    assert [r["name"] for r in rows] == ["PM Standup"]
    assert "secret" not in rows[0]  # secrets never appear in listings
    assert rows[0]["has_secret"] is True


def test_create_validates_type(tmp_db):
    seed_user("usr_1")
    with pytest.raises(ValueError):
        configs.create("usr_1", type="not_a_real_type", name="X")


def test_create_rejects_empty_name(tmp_db):
    seed_user("usr_1")
    with pytest.raises(ValueError):
        configs.create("usr_1", type="slack", name="   ")


def test_create_without_secret(tmp_db):
    seed_user("usr_1")
    c = configs.create("usr_1", type="slack", name="No secret")
    assert c["has_secret"] is False
    assert configs.get_secret(c["id"], owner_user_id="usr_1") is None


def test_config_json_roundtrip(tmp_db):
    seed_user("usr_1")
    c = configs.create("usr_1", type="slack", name="Tagged", config={"routing_tag": "jira"})
    got = configs.get(c["id"], "usr_1")
    assert got is not None
    assert got["config"] == {"routing_tag": "jira"}


def test_list_is_owner_scoped(tmp_db):
    seed_user("usr_1")
    seed_user("usr_2", email="two@x.com")
    configs.create("usr_1", type="slack", name="Mine")
    assert configs.list_for_user("usr_2") == []


def test_delete_is_owner_scoped(tmp_db):
    seed_user("usr_1")
    seed_user("usr_2", email="two@x.com")
    c = configs.create("usr_1", type="slack", name="Mine")

    assert configs.delete(c["id"], "usr_2") is False  # not the owner
    assert configs.delete(c["id"], "usr_1") is True
    assert configs.list_for_user("usr_1") == []


def test_owned_by_and_get_secret(tmp_db):
    seed_user("usr_1")
    seed_user("usr_2", email="two@x.com")
    c = configs.create("usr_1", type="slack", name="Mine", secret=_SECRET)

    assert configs.owned_by(c["id"], "usr_1") is True
    assert configs.owned_by(c["id"], "usr_2") is False
    assert configs.owned_by("dst_missing", "usr_1") is False

    assert configs.get_secret(c["id"], owner_user_id="usr_1") == _SECRET
    # ownership enforced — wrong owner gets nothing
    assert configs.get_secret(c["id"], owner_user_id="usr_2") is None


def test_secret_encrypted_at_rest(tmp_db):
    """The raw column holds ciphertext, not the plaintext secret — but the
    repo decrypts transparently on read."""
    from sqlalchemy import text

    from app.db.session import session

    seed_user("usr_1")
    c = configs.create("usr_1", type="slack", name="Mine", secret=_SECRET)

    with session() as s:
        raw = s.execute(text("SELECT secret FROM destination_configs LIMIT 1")).scalar_one()
    assert _SECRET.encode() not in bytes(raw)  # not stored in the clear
    assert configs.get_secret(c["id"], owner_user_id="usr_1") == _SECRET
