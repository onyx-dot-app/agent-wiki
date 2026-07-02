"""Slack dispatch in ``_record_fire``.

A fire is always recorded to the events table. Slack delivery is an additive
outbound POST that only happens when the action's destination config resolves
to a slack secret, and a Slack failure must never lose the recorded event.
"""
from __future__ import annotations

import pytest

from app.models.wiki import ChangeKind
from app.slack import client as slack_client
from app.tasks.triggers import _record_fire
from app.triggers import destination_configs as dest_configs
from app.triggers.engine import TriggerAction, TriggerRecord

from tests._seed import list_events, seed_user

_HOOK = "https://hooks.slack.com/services/EXAMPLE"
_OWNER = "usr_1"


def _trigger(*, destination_config_id: str | None) -> TriggerRecord:
    return TriggerRecord(
        id="trg_1",
        owner_user_id=_OWNER,
        scope_path="projects/foo.md",
        kind="delta",
        nl_description="fire when status changes",
        actions=[
            TriggerAction(destination_config_id=destination_config_id, message="status changed")
        ],
        enabled=True,
        file_path=None,
        created_at=None,
        last_edited_at=None,
    )


def _fire(trigger: TriggerRecord, *, message: str = "Status flipped to done") -> None:
    _record_fire(
        trigger=trigger,
        action=trigger.actions[0],
        doc_path="projects/foo.md",
        sha="abc123",
        change_kind=ChangeKind.EDIT,
        reason="status flipped",
        instruction="say what changed",
        rendered_message=message,
        actor="U <u@x.com>",
    )


@pytest.fixture
def _captured_post(monkeypatch):
    """Capture webhook POSTs so no network call fires."""
    calls: list[dict] = []

    def fake_post(*, webhook_url: str, text: str) -> None:
        calls.append({"webhook_url": webhook_url, "text": text})

    monkeypatch.setattr(slack_client, "post_message", fake_post)
    return calls


def _last_fire() -> dict:
    fires = list_events(kind="trigger.fire")
    assert fires, "expected a trigger.fire event"
    return fires[0]  # newest first


def _slack_config(owner: str = _OWNER, *, secret: str | None = _HOOK) -> str:
    return dest_configs.create(owner, type="slack", name="PM Standup", secret=secret)["id"]


def test_slack_config_records_event_and_posts(tmp_db, _captured_post):
    seed_user(_OWNER)
    cid = _slack_config()

    _fire(_trigger(destination_config_id=cid))

    payload = _last_fire()["payload"]
    assert payload["destination_type"] == "slack"
    assert payload["destination_config_id"] == cid
    assert payload["message"] == "Status flipped to done"

    assert len(_captured_post) == 1
    assert _captured_post[0]["webhook_url"] == _HOOK
    assert _captured_post[0]["text"] == "Status flipped to done"


def test_event_log_records_but_does_not_post(tmp_db, _captured_post):
    seed_user(_OWNER)

    _fire(_trigger(destination_config_id=None))

    assert _last_fire()["payload"]["destination_type"] == "event_log"
    assert _captured_post == []


def test_bot_channel_config_never_hits_webhook_path(tmp_db, _captured_post):
    """A channel-target config (no secret) must not post through the webhook
    client, even when the owner has no bot connection."""
    seed_user(_OWNER)
    cid = dest_configs.create(
        _OWNER, type="slack", name="Chan", config={"channel_id": "C1"}
    )["id"]

    _fire(_trigger(destination_config_id=cid))

    assert _last_fire()["payload"]["destination_type"] == "slack"
    assert _captured_post == []


def test_config_not_owned_records_but_does_not_post(tmp_db, _captured_post):
    seed_user(_OWNER)
    seed_user("usr_other", email="other@x.com")
    other_cid = _slack_config("usr_other")

    # trg owner is _OWNER but references another user's config, so it must not post.
    _fire(_trigger(destination_config_id=other_cid))

    assert _last_fire()["payload"]["destination_type"] == "unknown"
    assert _captured_post == []


def test_slack_failure_still_records_event(tmp_db, monkeypatch):
    seed_user(_OWNER)
    cid = _slack_config()

    def boom(*, webhook_url: str, text: str) -> None:
        raise slack_client.SlackApiError("slack is down")

    monkeypatch.setattr(slack_client, "post_message", boom)

    # Must not raise: the fire is recorded before dispatch.
    _fire(_trigger(destination_config_id=cid))

    assert _last_fire()["payload"]["destination_type"] == "slack"
