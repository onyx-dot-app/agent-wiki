"""Trigger ↔ Slack-channel wiring at the repo layer.

Covers ``_validate_slack_webhook``: a ``slack`` destination requires a
webhook the owner owns, and a flip away from slack clears the reference.
Uses ``tmp_repo`` because ``triggers_repo.create`` commits the YAML to git.
"""
from __future__ import annotations

import pytest

from app.slack import webhooks as slack_webhooks
from app.triggers import repo as triggers_repo

from tests._seed import seed_user

_HOOK = "https://hooks.slack.com/services/EXAMPLE"


def _create(owner: str, **kw):
    return triggers_repo.create(
        owner_user_id=owner,
        scope_path="a.md",
        nl_description="when status changes",
        message="status changed",
        **kw,
    )


def test_slack_trigger_with_owned_webhook(tmp_repo):
    seed_user("usr_1")
    wh = slack_webhooks.create("usr_1", "PM Standup", _HOOK)
    t = _create("usr_1", destination="slack", slack_webhook_id=wh["id"])
    assert t["destination"] == "slack"
    assert t["slack_webhook_id"] == wh["id"]


def test_slack_trigger_without_webhook_rejected(tmp_repo):
    seed_user("usr_1")
    with pytest.raises(ValueError):
        _create("usr_1", destination="slack")


def test_slack_trigger_rejects_other_users_webhook(tmp_repo):
    seed_user("usr_1")
    seed_user("usr_2", email="two@x.com")
    wh = slack_webhooks.create("usr_2", "Theirs", _HOOK)
    with pytest.raises(ValueError):
        _create("usr_1", destination="slack", slack_webhook_id=wh["id"])


def test_flip_to_event_log_clears_webhook(tmp_repo):
    seed_user("usr_1")
    wh = slack_webhooks.create("usr_1", "PM Standup", _HOOK)
    t = _create("usr_1", destination="slack", slack_webhook_id=wh["id"])

    updated = triggers_repo.update(t["id"], destination="event_log")
    assert updated is not None
    assert updated["destination"] == "event_log"
    assert updated["slack_webhook_id"] is None


def test_rebuild_preserves_slack_webhook_id(tmp_repo):
    # Regression: rebuild_from_filesystem dropped slack_webhook_id when
    # reconstructing the cache row, so any rebuild (boot, path move) made the
    # channel show as "(channel removed)" even though the YAML still had it.
    seed_user("usr_1")
    wh = slack_webhooks.create("usr_1", "PM Standup", _HOOK)
    t = _create("usr_1", destination="slack", slack_webhook_id=wh["id"])

    triggers_repo.rebuild_from_filesystem()

    rebuilt = triggers_repo.get(t["id"])
    assert rebuilt is not None
    assert rebuilt["destination"] == "slack"
    assert rebuilt["slack_webhook_id"] == wh["id"]
