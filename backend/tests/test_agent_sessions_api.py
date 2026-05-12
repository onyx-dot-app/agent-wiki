"""HTTP API for agent_sessions: list, heartbeat, cli-session, spawn-ok, close."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.auth import mcp_tokens as tokens_repo
from app.db.session import init_db
from app.launchers import sessions as sessions_repo
from app.main import create_app

from tests._auth import login_fastapi
from tests._seed import seed_user


def _get_session_dict(sid):
    from app.launchers import sessions as _sr
    row = _sr.get(sid)
    assert row is not None
    return row


@pytest.fixture
def client(tmp_config):
    init_db()
    return TestClient(create_app())


def _seed_session(uid: str, **kw) -> str:
    return sessions_repo.create(
        user_id=uid,
        tool_id=kw.get("tool_id", "claude-code"),
        first_turn_prompt=kw.get("first_turn_prompt", "x"),
        wiki_path=kw.get("wiki_path"),
        working_dir=kw.get("working_dir"),
    )


def test_list_sessions_for_user(client):
    uid = seed_user()
    login_fastapi(client, uid)
    a = _seed_session(uid, wiki_path="match.md")
    b = _seed_session(uid, wiki_path="other.md")
    res = client.get("/api/agent-sessions")
    assert res.status_code == 200, res.text
    ids = {s["id"] for s in res.json()["sessions"]}
    assert ids == {a, b}


def test_list_sessions_filtered_by_wiki_path(client):
    uid = seed_user()
    login_fastapi(client, uid)
    a = _seed_session(uid, wiki_path="match.md")
    _seed_session(uid, wiki_path="other.md")
    res = client.get("/api/agent-sessions?wiki_path=match.md")
    assert {s["id"] for s in res.json()["sessions"]} == {a}


def test_list_sessions_only_returns_callers_own(client):
    a = seed_user("usr_a", email="a@x.com")
    b = seed_user("usr_b", email="b@x.com")
    sid_a = _seed_session(a)
    _seed_session(b)
    login_fastapi(client, a)
    res = client.get("/api/agent-sessions")
    assert {s["id"] for s in res.json()["sessions"]} == {sid_a}


def test_heartbeat_updates_last_activity(client):
    uid = seed_user()
    login_fastapi(client, uid)
    sid = _seed_session(uid)
    sessions_repo.mark_active(sid, machine_id="m")
    before = _get_session_dict(sid)["last_activity_at"]
    res = client.post(f"/api/agent-sessions/{sid}/heartbeat")
    assert res.status_code == 204
    after = _get_session_dict(sid)["last_activity_at"]
    assert after >= before


def test_heartbeat_cross_user_forbidden(client):
    a = seed_user("usr_a", email="a@x.com")
    b = seed_user("usr_b", email="b@x.com")
    sid = _seed_session(a)
    login_fastapi(client, b)
    res = client.post(f"/api/agent-sessions/{sid}/heartbeat")
    assert res.status_code == 403


def test_heartbeat_with_bearer_succeeds(client):
    """AF#2 — helper drives heartbeat with bearer, not cookie."""
    uid = seed_user()
    sid = _seed_session(uid)
    _, raw_token = tokens_repo.create(uid, "launcher")
    res = client.post(
        f"/api/agent-sessions/{sid}/heartbeat",
        headers={"Authorization": f"Bearer {raw_token}"},
    )
    assert res.status_code == 204


def test_cli_session_id_post(client):
    uid = seed_user()
    login_fastapi(client, uid)
    sid = _seed_session(uid)
    res = client.post(
        f"/api/agent-sessions/{sid}/cli-session",
        json={"cli_session_id": "cli_xyz"},
    )
    assert res.status_code == 204
    assert _get_session_dict(sid)["cli_session_id"] == "cli_xyz"


def test_spawn_ok_post(client):
    """R9#1 — helper acks the spawn so sweep doesn't mark failed."""
    uid = seed_user()
    login_fastapi(client, uid)
    sid = _seed_session(uid)
    assert _get_session_dict(sid)["spawn_ok_at"] is None
    res = client.post(f"/api/agent-sessions/{sid}/spawn-ok")
    assert res.status_code == 204
    assert _get_session_dict(sid)["spawn_ok_at"] is not None


def test_close_with_user_reason_marks_closed(client):
    uid = seed_user()
    login_fastapi(client, uid)
    sid = _seed_session(uid)
    res = client.post(f"/api/agent-sessions/{sid}/close", json={"reason": "user_clicked"})
    assert res.status_code == 204
    assert _get_session_dict(sid)["status"] == "closed"


def test_close_with_error_reason_marks_failed(client):
    """AF#11 — error reasons → status=failed."""
    uid = seed_user()
    login_fastapi(client, uid)
    sid = _seed_session(uid)
    res = client.post(
        f"/api/agent-sessions/{sid}/close",
        json={"reason": "cli_not_found"},
    )
    assert res.status_code == 204
    assert _get_session_dict(sid)["status"] == "failed"
