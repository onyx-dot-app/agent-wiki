"""Tests for the Slack dispatch branch in ``_record_fire``.

A fire is *always* recorded to the events table; Slack delivery is an
additive outbound POST that only happens when the destination is ``slack``
and the trigger references a webhook the owner still owns — and a Slack
failure must never lose the recorded event.
"""
from __future__ import annotations

import pytest

from app.models.wiki import ChangeKind
from app.slack import client as slack_client
from app.slack import webhooks as slack_webhooks
from app.tasks.triggers import _record_fire
from app.triggers.engine import TriggerRecord

from tests._seed import list_events, seed_user

_HOOK = "https://hooks.slack.com/services/T00/B00/XXXXXXXX"
_OWNER = "usr_1"


def _trigger(*, destination: str, slack_webhook_id: str | None = None) -> TriggerRecord:
    return TriggerRecord(
        id="trg_1",
        owner_user_id=_OWNER,
        scope_path="projects/foo.md",
        kind="delta",
        nl_description="fire when status changes",
        message="status changed",
        destination=destination,
        slack_webhook_id=slack_webhook_id,
        enabled=True,
        file_path=None,
        created_at=None,
        last_edited_at=None,
    )


def _fire(trigger: TriggerRecord, *, message: str = "Status flipped to done") -> None:
    _record_fire(
        trigger=trigger,
        doc_path="projects/foo.md",
        sha="abc123",
        change_kind=ChangeKind.EDIT,
        reason="status flipped",
        instruction="say what changed",
        rendered_message=message,
        destination=trigger.destination,
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


def test_slack_destination_records_event_and_posts(tmp_db, _captured_post):
    seed_user(_OWNER)
    wh = slack_webhooks.create(_OWNER, "PM Standup", _HOOK)

    _fire(_trigger(destination="slack", slack_webhook_id=wh["id"]))

    payload = _last_fire()["payload"]
    assert payload["destination"] == "slack"
    assert payload["message"] == "Status flipped to done"

    assert len(_captured_post) == 1
    assert _captured_post[0]["webhook_url"] == _HOOK
    assert _captured_post[0]["text"] == "Status flipped to done"


def test_slack_without_channel_records_but_does_not_post(tmp_db, _captured_post):
    seed_user(_OWNER)

    _fire(_trigger(destination="slack", slack_webhook_id=None))

    assert _last_fire()["payload"]["destination"] == "slack"
    assert _captured_post == []  # no channel → recorded only


def test_slack_channel_not_owned_records_but_does_not_post(tmp_db, _captured_post):
    seed_user(_OWNER)
    seed_user("usr_other", email="other@x.com")
    other_wh = slack_webhooks.create("usr_other", "Theirs", _HOOK)

    # trg owner is _OWNER but references another user's webhook → must not post.
    _fire(_trigger(destination="slack", slack_webhook_id=other_wh["id"]))

    assert _last_fire()["payload"]["destination"] == "slack"
    assert _captured_post == []


def test_event_log_destination_never_posts(tmp_db, _captured_post):
    seed_user(_OWNER)

    _fire(_trigger(destination="event_log"))

    assert _last_fire()["payload"]["destination"] == "event_log"
    assert _captured_post == []


def test_slack_failure_still_records_event(tmp_db, monkeypatch):
    seed_user(_OWNER)
    wh = slack_webhooks.create(_OWNER, "PM Standup", _HOOK)

    def boom(*, webhook_url: str, text: str) -> None:
        raise slack_client.SlackApiError("slack is down")

    monkeypatch.setattr(slack_client, "post_message", boom)

    # Must not raise — the fire is already recorded before dispatch.
    _fire(_trigger(destination="slack", slack_webhook_id=wh["id"]))

    assert _last_fire()["payload"]["destination"] == "slack"
