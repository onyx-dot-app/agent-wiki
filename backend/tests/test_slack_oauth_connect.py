"""Connect-Slack OAuth flow: admin-configured credentials gate the flow, the
callback exchanges the code and stores the connection, and state misuse
bounces with an error instead of connecting."""
from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.slack import app_settings, connections

from tests._auth import login_fastapi
from tests._seed import seed_user


@pytest.fixture
def client(tmp_repo):
    return TestClient(create_app())

_EXCHANGE_OK = {
    "ok": True,
    "access_token": "xoxb-exchanged-token-123456789",
    "scope": "chat:write,im:write",
    "team": {"id": "T123", "name": "Onyx Team"},
    "authed_user": {"id": "U777"},
}


def _configure() -> None:
    app_settings.upsert(client_id="123.456", client_secret="shhh-secret")


def _admin(client) -> str:
    uid = seed_user(uid="usr_admin", email="admin@x.com", is_admin=True)
    login_fastapi(client, uid)
    return uid


def test_admin_settings_roundtrip_masks_secret(client):
    _admin(client)
    res = client.put(
        "/api/admin/slack-app",
        json={"client_id": "123.456", "client_secret": "shhh-secret-long-enough"},
    )
    assert res.status_code == 200, res.json()
    body = res.json()
    assert body["client_id"] == "123.456"
    assert body["client_secret_set"] is True
    assert "shhh" not in body["client_secret_hint"] or len(body["client_secret_hint"]) < 12

    # Empty secret keeps the stored value; explicit null clears it.
    res = client.put("/api/admin/slack-app", json={"client_id": "123.456", "client_secret": ""})
    assert res.json()["client_secret_set"] is True
    res = client.put("/api/admin/slack-app", json={"client_id": "123.456", "client_secret": None})
    assert res.json()["client_secret_set"] is False


def test_status_reflects_configuration_and_connection(client):
    uid = seed_user("usr_1")
    login_fastapi(client, uid)

    res = client.get("/api/connectors/slack")
    assert res.json() == {
        "configured": False,
        "connected": False,
        "team_name": None,
        "token_display": None,
        "connect_url": None,
    }

    _configure()
    connections.upsert(
        user_id=uid, team_id="T123", team_name="Onyx Team",
        slack_user_id="U777", bot_token="xoxb-abc", scope=None,
    )
    body = client.get("/api/connectors/slack").json()
    assert body["configured"] is True
    assert body["connected"] is True
    assert body["team_name"] == "Onyx Team"


def test_start_is_dark_until_configured(client):
    uid = seed_user("usr_1")
    login_fastapi(client, uid)
    res = client.get("/api/connectors/slack/start", follow_redirects=False)
    assert res.status_code == 404


def test_start_redirects_to_slack_authorize(client):
    uid = seed_user("usr_1")
    login_fastapi(client, uid)
    _configure()
    res = client.get("/api/connectors/slack/start", follow_redirects=False)
    assert res.status_code == 302
    target = urlparse(res.headers["location"])
    assert target.hostname == "slack.com"
    assert target.path == "/oauth/v2/authorize"
    q = parse_qs(target.query)
    assert q["client_id"] == ["123.456"]
    assert q["state"][0].startswith("slkst_")
    assert q["redirect_uri"][0].endswith("/api/connectors/slack/callback")


def _start_and_get_state(client) -> str:
    res = client.get("/api/connectors/slack/start", follow_redirects=False)
    return parse_qs(urlparse(res.headers["location"]).query)["state"][0]


def test_callback_stores_connection(client, monkeypatch):
    uid = seed_user("usr_1")
    login_fastapi(client, uid)
    _configure()
    state = _start_and_get_state(client)

    captured: dict = {}

    def fake_exchange(**kwargs):
        captured.update(kwargs)
        return _EXCHANGE_OK

    monkeypatch.setattr(
        "app.api.slack_connect.slack_client.exchange_oauth_code", fake_exchange
    )
    res = client.get(
        f"/api/connectors/slack/callback?code=c0de&state={state}",
        follow_redirects=False,
    )
    assert res.status_code == 302
    assert "slack_connect=ok" in res.headers["location"]
    assert captured["code"] == "c0de"

    row = connections.get(uid, "T123")
    assert row is not None
    assert row["team_name"] == "Onyx Team"
    assert row["slack_user_id"] == "U777"
    assert connections.get_bot_token(uid, "T123") == _EXCHANGE_OK["access_token"]


def test_callback_rejects_bad_state(client):
    uid = seed_user("usr_1")
    login_fastapi(client, uid)
    _configure()
    res = client.get(
        "/api/connectors/slack/callback?code=c0de&state=slkst_bogus",
        follow_redirects=False,
    )
    assert res.status_code == 302
    assert "slack_connect=error" in res.headers["location"]
    assert connections.list_for_user(uid) == []


def test_callback_handles_user_decline(client):
    uid = seed_user("usr_1")
    login_fastapi(client, uid)
    _configure()
    state = _start_and_get_state(client)
    res = client.get(
        f"/api/connectors/slack/callback?error=access_denied&state={state}",
        follow_redirects=False,
    )
    assert res.status_code == 302
    assert "slack_connect=declined" in res.headers["location"]
    assert connections.list_for_user(uid) == []


def test_disconnect_removes_rows(client):
    uid = seed_user("usr_1")
    login_fastapi(client, uid)
    connections.upsert(
        user_id=uid, team_id="T123", team_name=None,
        slack_user_id="U1", bot_token="xoxb-a", scope=None,
    )
    res = client.delete("/api/connectors/slack")
    assert res.json() == {"disconnected": True}
    assert connections.list_for_user(uid) == []
    assert client.delete("/api/connectors/slack").json() == {"disconnected": False}


def test_settings_repr_redacts_secret():
    from pydantic import SecretStr

    s = app_settings.SlackAppSettings(
        client_id="123.456", client_secret=SecretStr("shhh-secret")
    )
    assert "shhh-secret" not in repr(s)
    assert "shhh-secret" not in str(s)
    assert s.configured


def test_callback_bounces_on_ok_without_token(client, monkeypatch):
    uid = seed_user("usr_1")
    login_fastapi(client, uid)
    _configure()
    state = _start_and_get_state(client)
    monkeypatch.setattr(
        "app.api.slack_connect.slack_client.exchange_oauth_code",
        lambda **kw: {"ok": True, "team": {"id": "T123"}},
    )
    res = client.get(
        f"/api/connectors/slack/callback?code=c0de&state={state}",
        follow_redirects=False,
    )
    assert res.status_code == 302
    assert "slack_connect=error" in res.headers["location"]
    assert connections.list_for_user(uid) == []


def test_callback_treats_non_decline_error_as_failure(client):
    uid = seed_user("usr_1")
    login_fastapi(client, uid)
    _configure()
    state = _start_and_get_state(client)
    res = client.get(
        f"/api/connectors/slack/callback?error=invalid_scope&state={state}",
        follow_redirects=False,
    )
    assert res.status_code == 302
    assert "slack_connect=error" in res.headers["location"]
    assert connections.list_for_user(uid) == []
