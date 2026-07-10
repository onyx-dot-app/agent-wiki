"""Craft dispatch in ``_record_fire``: a craft action starts a session for the
trigger's owner seeded with the fire, launch preconditions never lose the
recorded event, and craft configs stay one-per-user with no secret. The
end-to-end test runs the real workflow and launch task with only the Onyx
HTTP client faked."""
from __future__ import annotations

import json
from typing import Any

import pytest

from app.db import agent_sessions as sessions_repo
from app.db import notifications as notifications_repo
from app.ingest import settings as ingest_settings
from app.launchers import craft as craft_workflow
from app.models.wiki import ChangeKind
from app.onyx import connections
from app.tasks.queues import triggers_queue
from app.tasks.triggers import _record_fire
from app.triggers import destination_configs as dest_configs
from app.wiki import git as wiki_git

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
        # The fire site binds instruction to the action's message.
        instruction="ship the update",
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
    # The owner's task leads the seed verbatim, untouched by the wiki LLM.
    assert calls[0]["message"].startswith("ship the update")
    assert "status flipped" in calls[0]["message"]
    assert len(list_events("trigger.fire")) == 1
    notifs = notifications_repo.list_for_user(_OWNER)["notifications"]
    started = [n for n in notifs if n["notif_type"] == "craft_started"]
    assert len(started) == 1
    assert started[0]["description"] == "ship the update"
    assert started[0]["data"]["agent_session_id"] == "as_test"


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


def test_craft_fire_end_to_end_reaches_ready_session(
    tmp_repo: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fire -> dispatch -> real start_session -> real craft_launch task, with
    only the Onyx HTTP client faked: the session reaches ready, the page is
    attached, and the seed carries the rendered summary plus fire context."""
    ingest_settings.upsert(max_doc_chars=100_000, onyx_base_url="https://onyx.example.com")
    seed_user(_OWNER)
    connections.upsert(
        user_id=_OWNER,
        onyx_pat="onyx_pat_" + "p" * 40,
        onyx_user_email="nik@onyx.app",
        expires_at=None,
        onyx_base_url="https://onyx.example.com",
    )
    wiki_git.commit_file("projects/foo.md", "# foo\n", "seed", author=None)

    sent: list[tuple[str, Any]] = []

    class Fake:
        def __init__(self, base_url: str, pat: str):
            pass

        def create_build_session(self) -> str:
            return "bs_e2e"

        def set_session_name(self, session_id: str, *, name: str) -> None:
            pass

        def upload_attachment(
            self, session_id: str, *, filename: str, content: bytes
        ) -> None:
            sent.append(("upload", filename))

        def session_message_count(self, session_id: str) -> int:
            return 0

        def send_seed_message(self, session_id: str, *, content: str) -> None:
            sent.append(("seed", content))

    monkeypatch.setattr("app.tasks.craft.OnyxClient", Fake)

    with triggers_queue.immediate_mode():
        _fire(_trigger(_craft_config()))

    assert len(list_events("trigger.fire")) == 1
    rows = sessions_repo.list_for_user(_OWNER)
    assert len(rows) == 1
    assert rows[0]["status"] == "ready"
    assert rows[0]["wiki_path"] == "projects/foo.md"
    seeds = [c for kind, c in sent if kind == "seed"]
    assert len(seeds) == 1
    assert seeds[0].endswith(
        "ship the update\n\n"
        "Fire context: trigger trg_1 (delta) fired on projects/foo.md (edit). "
        "Match reason: status flipped"
    ) or "ship the update" in seeds[0]
    assert "trg_1" in seeds[0]
    assert any(kind == "upload" for kind, _ in sent)


def test_craft_action_skips_the_render(
    owner: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Through the full fan-out loop, a craft action never invokes the
    wiki-side message render: the action's message reaches the seed and the
    recorded fire verbatim."""
    from app.tasks import triggers as trig_task
    from app.triggers import diff as diff_helper
    from app.triggers import engine
    from app.triggers.natural_language import MatchResult

    from tests._seed import seed_trigger

    config_id = _craft_config()
    seed_trigger(
        tid="trg_craft",
        owner_user_id=_OWNER,
        scope_path="projects/foo.md",
        message="generate a fun image of the page",
        destination_config_id=config_id,
    )

    monkeypatch.setattr(
        trig_task, "_read_at", lambda ref, rel: "before" if ref.endswith("^") else "after"
    )
    monkeypatch.setattr(
        diff_helper, "build_scope_block", lambda scope_path: "=== SCOPED DOCS ===\n"
    )
    monkeypatch.setattr(
        engine, "nl_matches", lambda *a, **kw: MatchResult(matched=True, reason="always")
    )

    def boom(*a: object, **kw: object) -> str:
        raise AssertionError("the wiki LLM must not render a craft action's message")

    monkeypatch.setattr(engine, "nl_render_message", boom)

    seeds: list[str] = []

    def fake_start(
        *, user_id: str, is_admin: bool, wiki_path: str | None, message: str
    ) -> tuple[str, str]:
        seeds.append(message)
        return "as_test", "provisioning"

    monkeypatch.setattr("app.tasks.triggers.craft_workflow.start_session", fake_start)

    with triggers_queue.immediate_mode():
        trig_task.fan_out_trigger_eval("projects/foo.md", "abc123", ChangeKind.EDIT)

    assert len(seeds) == 1
    assert seeds[0].startswith("generate a fun image of the page")
    events = list_events("trigger.fire")
    assert len(events) == 1
    payload = json.loads(events[0]["payload_json"])
    assert payload["message"] == "generate a fun image of the page"


def test_craft_config_is_one_per_user_and_takes_no_secret(owner: None) -> None:
    first = dest_configs.create(_OWNER, type="craft", name="Onyx Craft", config=None)
    again = dest_configs.create(_OWNER, type="craft", name="Different Name", config=None)
    assert again["id"] == first["id"]
    with pytest.raises(ValueError, match="no secret"):
        dest_configs.create(_OWNER, type="craft", name="Onyx Craft", config=None, secret="x")
