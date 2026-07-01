"""Reconcile of legacy slack triggers into destination configs: a legacy YAML
that names a slack_webhook_id gets a mirrored destination config and is
rewritten to reference it, and the pass is idempotent.
"""
from __future__ import annotations

from typing import Any

import yaml

from app.slack import webhooks as slack_webhooks
from app.triggers import destination_configs as dest_configs
from app.triggers import storage
from app.triggers.reconcile import reconcile_legacy_slack_triggers
from app.wiki import git as wiki_git

from tests._seed import seed_user

_HOOK = "https://hooks.slack.com/services/EXAMPLE"


def _write_legacy(path: str, body: dict[str, Any]) -> None:
    wiki_git.commit_file(path, yaml.safe_dump(body, sort_keys=False), f"seed {path}", author=None)


def test_reconcile_migrates_slack_trigger(tmp_repo):
    seed_user("usr_1")
    wh = slack_webhooks.create("usr_1", "PM Standup", _HOOK)
    path = ".trigger_trg_legacy.yaml"
    _write_legacy(path, {
        "id": "trg_legacy", "owner_user_id": "usr_1", "scope_path": "a.md",
        "kind": "delta", "nl_description": "fire", "message": "hi",
        "destination": "slack", "slack_webhook_id": wh["id"], "enabled": True,
    })

    assert reconcile_legacy_slack_triggers() == 1

    configs = dest_configs.list_for_user("usr_1")
    assert len(configs) == 1
    cfg = configs[0]
    assert cfg["type"] == "slack"
    assert cfg["name"] == "PM Standup"
    assert dest_configs.get_secret(cfg["id"], owner_user_id="usr_1") == _HOOK

    data = storage.read_trigger(path)
    assert data["actions"] == [{"destination_config_id": cfg["id"], "message": "hi"}]

    # Idempotent: a second pass rewrites nothing and creates no duplicate config.
    assert reconcile_legacy_slack_triggers() == 0
    assert len(dest_configs.list_for_user("usr_1")) == 1


def test_reconcile_migrates_event_log_trigger(tmp_repo):
    seed_user("usr_1")
    path = ".trigger_trg_ev.yaml"
    _write_legacy(path, {
        "id": "trg_ev", "owner_user_id": "usr_1", "scope_path": "a.md",
        "kind": "delta", "nl_description": "fire", "message": "hi",
        "destination": "event_log", "enabled": True,
    })
    assert reconcile_legacy_slack_triggers() == 1
    data = storage.read_trigger(path)
    assert data["actions"] == [{"destination_config_id": None, "message": "hi"}]
    assert dest_configs.list_for_user("usr_1") == []


def test_reconcile_warns_when_webhook_missing(tmp_repo, caplog):
    """A slack trigger whose source webhook is gone degrades to event-log only,
    loudly: the drop is logged with the file, owner, and webhook id."""
    import logging

    seed_user("usr_1")
    path = ".trigger_trg_gone.yaml"
    _write_legacy(path, {
        "id": "trg_gone", "owner_user_id": "usr_1", "scope_path": "a.md",
        "kind": "delta", "nl_description": "fire", "message": "hi",
        "destination": "slack", "slack_webhook_id": "wh_deleted", "enabled": True,
    })

    with caplog.at_level(logging.WARNING, logger="app.triggers.reconcile"):
        assert reconcile_legacy_slack_triggers() == 1

    data = storage.read_trigger(path)
    assert data["actions"] == [{"destination_config_id": None, "message": "hi"}]
    assert dest_configs.list_for_user("usr_1") == []
    warning = next(r for r in caplog.records if "wh_deleted" in r.getMessage())
    assert path in warning.getMessage()
    assert "usr_1" in warning.getMessage()
