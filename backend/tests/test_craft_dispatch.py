"""Craft dispatch in ``_record_fire``: a craft action starts a session for the
trigger's owner seeded with the fire, launch preconditions never lose the
recorded event, and craft configs stay one-per-user with no secret."""
from __future__ import annotations

from typing import Any

import pytest

from app.launchers import craft as craft_workflow
from app.models.wiki import ChangeKind
from app.tasks.triggers import _record_fire
from app.triggers import destination_configs as dest_configs

from tests._seed import list_events, seed_user
from app.triggers.engine import TriggerAction, TriggerRecord

_OWNER = "usr_1"


def _craft_config() -> str:
    cfg = dest_configs.create(_OWNER, type="craft", name="Onyx Craft", config=None)
    return cfg["id"]


def _trigger(config_id: str) -> TriggerRecord:
    return TriggerRecord(
        id="trg_1",
        owner_user_id=_OWNER,
        scope_path="projects/foo.md",
        kind="delta",
        nl_description="fire when status changes",
        actions=[TriggerAction(destination_config_id=config_id, message="ship the update")],
        enabled=True,
        file_path=None,
        created_at=None,
        last_edited_at=None,
    )


def _fire(trigger: TriggerRecord) -> None:
    _record_fire(
        trigger=trigger,
        action=trigger.actions[0],
        doc_path="projects/foo.md",
        sha="abc123",
        change_kind=ChangeKind.EDIT,
        reason="status flipped",
        instruction="say what changed",
        rendered_message="Status flipped to done",
        actor="U <u@x.com>",
    )


@pytest.fixture
def owner(tmp_db: object) -> None:
    seed_user(_OWNER, is_admin=False)


def test_craft_fire_starts_session_for_owner(
    owner: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[dict[str, Any]] = []

    def fake_start(
        *, user_id: str, is_admin: bool, wiki_path: str | None, message: str
    ) -> tuple[str, str]:
        calls.append(
            {"user_id": user_id, "is_admin": is_admin, "wiki_path": wiki_path, "message": message}
        )
        return "as_test", "provisioning"

    monkeypatch.setattr("app.tasks.triggers.craft_workflow.start_session", fake_start)
    _fire(_trigger(_craft_config()))

    assert len(calls) == 1
    assert calls[0]["user_id"] == _OWNER
    assert calls[0]["wiki_path"] == "projects/foo.md"
    assert "Status flipped to done" in calls[0]["message"]
    assert "status flipped" in calls[0]["message"]
    assert len(list_events("trigger.fire")) == 1


def test_craft_launch_error_keeps_fire(
    owner: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_start(**_kw: object) -> tuple[str, str]:
        raise craft_workflow.CraftNotConnected()

    monkeypatch.setattr("app.tasks.triggers.craft_workflow.start_session", fake_start)
    _fire(_trigger(_craft_config()))
    assert len(list_events("trigger.fire")) == 1


def test_craft_transport_error_never_fails_the_task(
    owner: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_start(**_kw: object) -> tuple[str, str]:
        raise RuntimeError("redis send failed")

    monkeypatch.setattr("app.tasks.triggers.craft_workflow.start_session", fake_start)
    _fire(_trigger(_craft_config()))
    assert len(list_events("trigger.fire")) == 1


def test_craft_config_is_one_per_user_and_takes_no_secret(owner: None) -> None:
    first = dest_configs.create(_OWNER, type="craft", name="Onyx Craft", config=None)
    again = dest_configs.create(_OWNER, type="craft", name="Different Name", config=None)
    assert again["id"] == first["id"]
    with pytest.raises(ValueError, match="no secret"):
        dest_configs.create(_OWNER, type="craft", name="Onyx Craft", config=None, secret="x")
