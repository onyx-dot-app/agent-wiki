"""Trigger destination-config validation and persistence: a trigger may
reference a destination config the owner owns, an unowned id is rejected, and
the reference survives a cache rebuild.
"""
from __future__ import annotations

from typing import Any

import pytest

from app.triggers import destination_configs as dest_configs
from app.triggers import repo as triggers_repo

from tests._seed import seed_user

_HOOK = "https://hooks.slack.com/services/EXAMPLE"


def _create(owner: str, *, destination_config_id: str | None = None) -> dict[str, Any]:
    return triggers_repo.create(
        owner_user_id=owner,
        scope_path="a.md",
        nl_description="fire",
        actions=[{"message": "m", "destination_config_id": destination_config_id}],
    )


def _slack_config(owner: str) -> str:
    return dest_configs.create(owner, type="slack", name="PM Standup", secret=_HOOK)["id"]


def test_trigger_with_owned_config(tmp_repo):
    seed_user("usr_1")
    cid = _slack_config("usr_1")
    t = _create("usr_1", destination_config_id=cid)
    assert t["actions"][0]["destination_config_id"] == cid


def test_trigger_rejects_unowned_config(tmp_repo):
    seed_user("usr_1")
    seed_user("usr_2", email="b@x.com")
    cid = _slack_config("usr_2")
    with pytest.raises(ValueError, match="destination_config_id"):
        _create("usr_1", destination_config_id=cid)


def test_flip_to_event_log_clears_config(tmp_repo):
    seed_user("usr_1")
    cid = _slack_config("usr_1")
    t = _create("usr_1", destination_config_id=cid)
    updated = triggers_repo.update(str(t["id"]), actions=[{"message": "m"}])
    assert updated is not None
    assert updated["actions"][0]["destination_config_id"] is None


def test_rebuild_preserves_destination_config_id(tmp_repo):
    seed_user("usr_1")
    cid = _slack_config("usr_1")
    t = _create("usr_1", destination_config_id=cid)
    triggers_repo.rebuild_from_filesystem()
    rebuilt = triggers_repo.get(str(t["id"]))
    assert rebuilt is not None
    assert rebuilt["actions"][0]["destination_config_id"] == cid
