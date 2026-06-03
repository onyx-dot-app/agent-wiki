"""Tests for app/slack/webhooks.py — the per-user Slack webhook registry."""
from __future__ import annotations

import pytest

from app.slack import webhooks as slack_webhooks

from tests._seed import seed_user

_HOOK = "https://hooks.slack.com/services/T00/B00/XXXXXXXX"


def test_create_then_list(tmp_db):
    seed_user("usr_1")
    wh = slack_webhooks.create("usr_1", "PM Standup", _HOOK)
    assert wh["id"].startswith("swh_")

    rows = slack_webhooks.list_for_user("usr_1")
    assert [r["name"] for r in rows] == ["PM Standup"]
    assert rows[0]["webhook_url"] == _HOOK


def test_list_is_owner_scoped(tmp_db):
    seed_user("usr_1")
    seed_user("usr_2", email="two@x.com")
    slack_webhooks.create("usr_1", "Mine", _HOOK)
    assert slack_webhooks.list_for_user("usr_2") == []


def test_create_rejects_non_slack_url(tmp_db):
    seed_user("usr_1")
    with pytest.raises(ValueError):
        slack_webhooks.create("usr_1", "Bad", "https://evil.example.com/x")


def test_create_rejects_empty_name(tmp_db):
    seed_user("usr_1")
    with pytest.raises(ValueError):
        slack_webhooks.create("usr_1", "   ", _HOOK)


def test_delete_is_owner_scoped(tmp_db):
    seed_user("usr_1")
    seed_user("usr_2", email="two@x.com")
    wh = slack_webhooks.create("usr_1", "Mine", _HOOK)

    # usr_2 can't delete usr_1's webhook
    assert slack_webhooks.delete(wh["id"], "usr_2") is False
    # owner can
    assert slack_webhooks.delete(wh["id"], "usr_1") is True
    assert slack_webhooks.list_for_user("usr_1") == []


def test_owned_by_and_get_url(tmp_db):
    seed_user("usr_1")
    seed_user("usr_2", email="two@x.com")
    wh = slack_webhooks.create("usr_1", "Mine", _HOOK)

    assert slack_webhooks.owned_by(wh["id"], "usr_1") is True
    assert slack_webhooks.owned_by(wh["id"], "usr_2") is False
    assert slack_webhooks.owned_by("swh_missing", "usr_1") is False

    assert slack_webhooks.get_url(wh["id"], owner_user_id="usr_1") == _HOOK
    # ownership enforced — wrong owner gets nothing
    assert slack_webhooks.get_url(wh["id"], owner_user_id="usr_2") is None
