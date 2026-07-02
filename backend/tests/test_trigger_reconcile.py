"""Boot reconcile of legacy trigger YAML: pre-registry files rewrite to the
actions shape, slack references fall back to event-log with a loud warning,
and the pass is idempotent."""
from __future__ import annotations

import logging
from typing import Any

import yaml

from app.triggers import storage
from app.triggers.reconcile import reconcile_legacy_slack_triggers
from app.wiki import git as wiki_git

from tests._seed import seed_user


def _write_legacy(path: str, body: dict[str, Any]) -> None:
    wiki_git.commit_file(path, yaml.safe_dump(body, sort_keys=False), f"seed {path}", author=None)


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

    # Idempotent: already-reshaped files are skipped.
    assert reconcile_legacy_slack_triggers() == 0


def test_reconcile_slack_reference_warns_and_degrades(tmp_repo, caplog):
    seed_user("usr_1")
    path = ".trigger_trg_slack.yaml"
    _write_legacy(path, {
        "id": "trg_slack", "owner_user_id": "usr_1", "scope_path": "a.md",
        "kind": "delta", "nl_description": "fire", "message": "hi",
        "destination": "slack", "slack_webhook_id": "swh_retired", "enabled": True,
    })

    with caplog.at_level(logging.WARNING, logger="app.triggers.reconcile"):
        assert reconcile_legacy_slack_triggers() == 1

    data = storage.read_trigger(path)
    assert data["actions"] == [{"destination_config_id": None, "message": "hi"}]
    warning = next(r for r in caplog.records if "swh_retired" in r.getMessage())
    assert path in warning.getMessage()
    assert "usr_1" in warning.getMessage()


def test_reconcile_skips_unreadable_and_non_trigger_files(tmp_repo):
    seed_user("usr_1")
    wiki_git.commit_file(".trigger_broken.yaml", "{not yaml: [", "seed broken", author=None)

    assert reconcile_legacy_slack_triggers() == 0


def test_reconcile_resolves_existing_mirror(tmp_repo):
    """A legacy slack reference whose mirrored config exists wires that config
    instead of degrading to event-log."""
    from app.triggers import destination_configs as dest_configs

    seed_user("usr_1")
    cfg = dest_configs.create(
        "usr_1",
        type="slack",
        name="PM Standup",
        config={"from_slack_webhook": "swh_abc123def456"},
        secret="https://hooks.slack.com/services/EXAMPLE",
    )
    path = ".trigger_trg_mirrored.yaml"
    _write_legacy(path, {
        "id": "trg_mirrored", "owner_user_id": "usr_1", "scope_path": "a.md",
        "kind": "delta", "nl_description": "fire", "message": "hi",
        "destination": "slack", "slack_webhook_id": "swh_abc123def456", "enabled": True,
    })

    assert reconcile_legacy_slack_triggers() == 1
    data = storage.read_trigger(path)
    assert data["actions"] == [{"destination_config_id": cfg["id"], "message": "hi"}]
