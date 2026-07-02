"""Bot-path Slack delivery: a fire posts to a channel config through the
owner's connection token, DMs the owner, appends the source line, and always
degrades to recorded-only when the connection or target is missing."""
from __future__ import annotations

from typing import Any

import pytest

from app.models.wiki import ChangeKind
from app.slack import client as slack_client, connections
from app.tasks.triggers import _record_fire
from app.triggers import destination_configs as dest_configs
from app.triggers.engine import TriggerAction, TriggerRecord

from tests._seed import list_events, seed_user

_OWNER = "usr_1"


def _trigger(config_id: str) -> TriggerRecord:
    return TriggerRecord(
        id="trg_1",
        owner_user_id=_OWNER,
        scope_path="projects/foo.md",
        kind="delta",
        nl_description="fire when status changes",
        actions=[TriggerAction(destination_config_id=config_id, message="status changed")],
        enabled=True,
        file_path=None,
        created_at=None,
        last_edited_at=None,
    )


def _fire(config_id: str) -> None:
    t = _trigger(config_id)
    _record_fire(
        trigger=t,
        action=t.actions[0],
        doc_path="projects/foo.md",
        sha="abc123",
        change_kind=ChangeKind.EDIT,
        reason="status flipped",
        instruction="say what changed",
        rendered_message="Status flipped to done",
        actor=None,
    )


def _connect_owner() -> None:
    connections.upsert(
        user_id=_OWNER,
        team_id="T123",
        team_name="Onyx Team",
        slack_user_id="U777",
        bot_token="xoxb-bot-token-123",
        scope=None,
    )


@pytest.fixture
def _bot_posts(monkeypatch):
    posts: list[dict[str, Any]] = []
    monkeypatch.setattr(
        slack_client,
        "post_chat_message",
        lambda *, bot_token, channel, text: posts.append(
            {"bot_token": bot_token, "channel": channel, "text": text}
        ),
    )
    return posts


def test_channel_config_posts_via_bot(tmp_db, _bot_posts):
    seed_user(_OWNER)
    _connect_owner()
    cfg = dest_configs.create(
        _OWNER, type="slack", name="Eng", config={"channel_id": "C42"}
    )

    _fire(cfg["id"])

    assert list_events(kind="trigger.fire")  # recorded regardless
    assert len(_bot_posts) == 1
    assert _bot_posts[0]["channel"] == "C42"
    assert _bot_posts[0]["bot_token"] == "xoxb-bot-token-123"
    assert _bot_posts[0]["text"].startswith("Status flipped to done")
    assert "Agent Wiki trigger on projects/foo.md" in _bot_posts[0]["text"]


def test_dm_config_opens_dm_and_posts(tmp_db, _bot_posts, monkeypatch):
    seed_user(_OWNER)
    _connect_owner()
    opened: list[str] = []
    monkeypatch.setattr(
        slack_client,
        "open_dm",
        lambda *, bot_token, slack_user_id: (opened.append(slack_user_id), "D99")[1],
    )
    cfg = dest_configs.create(_OWNER, type="slack", name="Me", config={"dm": True})

    _fire(cfg["id"])

    assert opened == ["U777"]
    assert len(_bot_posts) == 1
    assert _bot_posts[0]["channel"] == "D99"


def test_bot_config_without_connection_records_only(tmp_db, _bot_posts):
    seed_user(_OWNER)
    cfg = dest_configs.create(
        _OWNER, type="slack", name="Eng", config={"channel_id": "C42"}
    )

    _fire(cfg["id"])

    assert list_events(kind="trigger.fire")
    assert _bot_posts == []


def test_bot_dispatch_failure_still_records(tmp_db, monkeypatch):
    seed_user(_OWNER)
    _connect_owner()
    monkeypatch.setattr(
        slack_client,
        "post_chat_message",
        lambda **kw: (_ for _ in ()).throw(slack_client.SlackApiError("down")),
    )
    cfg = dest_configs.create(
        _OWNER, type="slack", name="Eng", config={"channel_id": "C42"}
    )

    _fire(cfg["id"])  # must not raise

    assert list_events(kind="trigger.fire")


def test_slack_config_requires_a_target(tmp_db):
    seed_user(_OWNER)
    with pytest.raises(ValueError, match="channel_id"):
        dest_configs.create(_OWNER, type="slack", name="Nowhere")
    # Each valid target shape is accepted.
    dest_configs.create(_OWNER, type="slack", name="Hook", secret="https://hooks.slack.com/x")
    dest_configs.create(_OWNER, type="slack", name="Chan", config={"channel_id": "C1"})
    dest_configs.create(_OWNER, type="slack", name="Me", config={"dm": True})


def test_slack_config_rejects_multiple_targets(tmp_db):
    seed_user(_OWNER)
    with pytest.raises(ValueError, match="exactly one"):
        dest_configs.create(
            _OWNER, type="slack", name="Both", config={"channel_id": "C1", "dm": True}
        )
    with pytest.raises(ValueError, match="exactly one"):
        dest_configs.create(
            _OWNER,
            type="slack",
            name="Both",
            secret="https://hooks.slack.com/x",
            config={"channel_id": "C1"},
        )


def test_list_channels_drops_entries_missing_id_or_name(monkeypatch):
    captured = {
        "ok": True,
        "channels": [
            {"id": "C1", "name": "eng", "is_private": False},
            {"id": None, "name": "ghost"},
            {"id": "C2"},
        ],
    }
    monkeypatch.setattr(slack_client, "_call_api", lambda *a, **kw: captured)
    out = slack_client.list_channels(bot_token="xoxb-x")
    assert out == [{"id": "C1", "name": "eng", "is_private": False}]
