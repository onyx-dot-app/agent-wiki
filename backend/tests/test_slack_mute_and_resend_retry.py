"""Muting a Slack connection pauses dispatch without disconnecting, and the
resend-verification 429 carries a Retry-After header for the UI countdown."""
from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.models.wiki import ChangeKind
from app.slack import connections as slack_connections
from app.tasks.triggers import _record_fire
from app.triggers import destination_configs as dest_configs
from app.triggers import destinations as destinations_repo
from app.triggers.engine import TriggerAction, TriggerRecord

from tests._auth import login_fastapi
from tests._seed import list_events, seed_user


@pytest.fixture
def client(tmp_db):
    return TestClient(create_app())


def _connect(uid: str) -> None:
    slack_connections.upsert(
        user_id=uid,
        team_id="T1",
        team_name="Onyx Team",
        slack_user_id="U1",
        bot_token="xoxb-secret",
        scope="chat:write",
    )


def _slack_trigger(uid: str) -> tuple[TriggerRecord, TriggerAction]:
    cfg = dest_configs.create(
        uid,
        type=destinations_repo.SLACK_ID,
        name="#general",
        config={"channel_id": "C1"},
    )
    action = TriggerAction(destination_config_id=cfg["id"], message="hi")
    trigger = TriggerRecord(
        id="trg_1",
        owner_user_id=uid,
        scope_path="a.md",
        kind="delta",
        nl_description="always",
        actions=[action],
        enabled=True,
        file_path=None,
        created_at=None,
        last_edited_at=None,
    )
    return trigger, action


def test_mute_endpoint_roundtrip(client):
    uid = seed_user(email="u@x.com")
    login_fastapi(client, uid)
    _connect(uid)

    r = client.put("/api/connectors/slack/mute", json={"muted": True})
    assert r.status_code == 200
    assert r.json()["muted"] is True
    assert r.json()["connected"] is True

    r = client.put("/api/connectors/slack/mute", json={"muted": False})
    assert r.json()["muted"] is False


def test_mute_without_connection_is_404(client):
    uid = seed_user(email="u@x.com")
    login_fastapi(client, uid)
    r = client.put("/api/connectors/slack/mute", json={"muted": True})
    assert r.status_code == 404


def test_muted_connection_skips_dispatch_but_records_fire(tmp_db, monkeypatch):
    uid = seed_user(email="u@x.com")
    _connect(uid)
    slack_connections.set_muted(uid, "T1", True)
    trigger, action = _slack_trigger(uid)

    posted: list[dict[str, Any]] = []
    monkeypatch.setattr(
        "app.tasks.triggers._post_slack_message",
        lambda **kw: posted.append(kw),
        raising=False,
    )

    _record_fire(
        trigger=trigger,
        action=action,
        doc_path="a.md",
        sha="abc",
        change_kind=ChangeKind.EDIT,
        reason="r",
        instruction="i",
        rendered_message="hi",
        actor=None,
    )

    assert posted == []
    kinds = [e["kind"] for e in list_events()]
    assert "trigger.fire" in kinds


def test_resend_429_carries_retry_after(client, monkeypatch):
    uid = seed_user(email="u@x.com")
    login_fastapi(client, uid)
    cfg = dest_configs.create(
        uid,
        type=destinations_repo.EMAIL_ID,
        name="me",
        config={"address": "me@x.com"},
    )
    monkeypatch.setattr(
        "app.triggers.email_verification.email_service.send", lambda **kw: None
    )
    first = client.post(f"/api/triggers/destination-configs/{cfg['id']}/resend-verify")
    assert first.status_code == 200
    second = client.post(f"/api/triggers/destination-configs/{cfg['id']}/resend-verify")
    assert second.status_code == 429
    retry = int(second.headers["Retry-After"])
    assert 1 <= retry <= 60
    assert second.json()["retry_after_seconds"] == retry


def test_muted_workspace_does_not_silence_other_workspace(tmp_db, monkeypatch):
    uid = seed_user(email="u@x.com")
    _connect(uid)  # T1
    slack_connections.upsert(
        user_id=uid,
        team_id="T2",
        team_name="Second Team",
        slack_user_id="U2",
        bot_token="xoxb-secret-2",
        scope="chat:write",
    )
    slack_connections.set_muted(uid, "T1", True)

    cfg = dest_configs.create(
        uid,
        type=destinations_repo.SLACK_ID,
        name="#general",
        config={"channel_id": "C2", "team_id": "T2"},
    )
    action = TriggerAction(destination_config_id=cfg["id"], message="hi")
    trigger = TriggerRecord(
        id="trg_2",
        owner_user_id=uid,
        scope_path="a.md",
        kind="delta",
        nl_description="always",
        actions=[action],
        enabled=True,
        file_path=None,
        created_at=None,
        last_edited_at=None,
    )

    posted: list[dict[str, Any]] = []
    monkeypatch.setattr(
        "app.tasks.triggers.slack_client.post_chat_message",
        lambda **kw: posted.append(kw),
    )

    _record_fire(
        trigger=trigger,
        action=action,
        doc_path="a.md",
        sha="abc",
        change_kind=ChangeKind.EDIT,
        reason="r",
        instruction="i",
        rendered_message="hi",
        actor=None,
    )

    assert len(posted) == 1
    assert posted[0]["channel"] == "C2"


def test_slack_config_creation_stamps_team_id(tmp_db):
    uid = seed_user(email="u@x.com")
    _connect(uid)
    cfg = dest_configs.create(
        uid,
        type=destinations_repo.SLACK_ID,
        name="#general",
        config={"channel_id": "C1"},
    )
    assert cfg["config"]["team_id"] == "T1"


def test_muted_connection_silences_webhook_destinations(tmp_db, monkeypatch):
    uid = seed_user(email="u@x.com")
    _connect(uid)
    slack_connections.set_muted(uid, "T1", True)
    cfg = dest_configs.create(
        uid,
        type=destinations_repo.SLACK_ID,
        name="webhook",
        secret="https://hooks.slack.example/T1/abc",
    )
    action = TriggerAction(destination_config_id=cfg["id"], message="hi")
    trigger = TriggerRecord(
        id="trg_3",
        owner_user_id=uid,
        scope_path="a.md",
        kind="delta",
        nl_description="always",
        actions=[action],
        enabled=True,
        file_path=None,
        created_at=None,
        last_edited_at=None,
    )

    posted: list[dict[str, Any]] = []
    monkeypatch.setattr(
        "app.tasks.triggers.slack_client.post_message",
        lambda **kw: posted.append(kw),
    )

    _record_fire(
        trigger=trigger,
        action=action,
        doc_path="a.md",
        sha="abc",
        change_kind=ChangeKind.EDIT,
        reason="r",
        instruction="i",
        rendered_message="hi",
        actor=None,
    )

    assert posted == []
    kinds = [e["kind"] for e in list_events()]
    assert "trigger.fire" in kinds
