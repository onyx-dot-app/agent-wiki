"""X-Agentwiki-Session header threading + cross-user 403 + + ."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.auth import mcp_tokens as tokens_repo
from app.db.models import AgentActivity
from app.db.session import init_db, session
from app.db import agent_sessions as sessions_repo
from app.main import create_app
from app.mcp_server import session as mcp_session
from app.wiki import git as wiki_git

from tests._auth import login_fastapi
from tests._seed import seed_user


@pytest.fixture
def client(tmp_repo):
    mcp_session.reset_for_tests()
    yield TestClient(create_app())
    mcp_session.reset_for_tests()


def _handshake(client, raw: str) -> dict[str, str]:
    auth = {"Authorization": f"Bearer {raw}"}
    res = client.post(
        "/api/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": "2025-03-26"},
        },
        headers=auth,
    )
    sess_id = res.headers["Mcp-Session-Id"]
    client.post(
        "/api/mcp",
        json={"jsonrpc": "2.0", "method": "notifications/initialized"},
        headers={**auth, "Mcp-Session-Id": sess_id},
    )
    return {**auth, "Mcp-Session-Id": sess_id}


def test_header_stamps_agent_session_id_and_agent_name(client):
    """X-Agentwiki-Session header → activity rows carry both
    agent_session_id AND agent_name=tool_id ."""
    init_db()
    uid = seed_user()
    _, raw = tokens_repo.create(uid, "k")
    wiki_git.commit_file("x.md", "# Hello\n", message="seed")
    agent_sid = sessions_repo.create(
        user_id=uid,
        tool_id="claude-code",
        first_turn_prompt="x",
        wiki_path="x.md",
        working_dir=None,
    )
    h = _handshake(client, raw)
    h["X-Agentwiki-Session"] = agent_sid

    # Issue read_doc → stamps a `read` activity.
    res = client.post(
        "/api/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 99,
            "method": "tools/call",
            "params": {"name": "read_doc", "arguments": {"path": "x.md"}},
        },
        headers=h,
    )
    assert res.status_code == 200, res.text

    with session() as s:
        rows = s.scalars(select(AgentActivity)).all()
    matched = [a for a in rows if a.agent_session_id == agent_sid and a.doc_path == "x.md"]
    assert len(matched) == 1
    assert matched[0].agent_name == "claude-code"


def test_file_activity_endpoint_includes_agent_session_id(client):
    """HTTP activity view surfaces the launcher session id for frontend consumers."""
    init_db()
    uid = seed_user()
    login_fastapi(client, uid)
    _, raw = tokens_repo.create(uid, "k")
    wiki_git.commit_file("x.md", "# Hello\n", message="seed")
    agent_sid = sessions_repo.create(
        user_id=uid,
        tool_id="claude-code",
        first_turn_prompt="x",
        wiki_path="x.md",
        working_dir=None,
    )

    headers = _handshake(client, raw)
    headers["X-Agentwiki-Session"] = agent_sid
    res = client.post(
        "/api/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 100,
            "method": "tools/call",
            "params": {"name": "read_doc", "arguments": {"path": "x.md"}},
        },
        headers=headers,
    )
    assert res.status_code == 200, res.text

    activity = client.get("/api/wiki/file/activity", params={"path": "x.md"})
    assert activity.status_code == 200, activity.text
    payload = activity.json()
    assert any(a["agent_session_id"] == agent_sid for a in payload["agents"])


def test_unknown_session_id_returns_400(client):
    init_db()
    uid = seed_user()
    _, raw = tokens_repo.create(uid, "k")
    h = _handshake(client, raw)
    h["X-Agentwiki-Session"] = "as_does_not_exist"
    res = client.post(
        "/api/mcp",
        json={"jsonrpc": "2.0", "id": 99, "method": "ping"},
        headers=h,
    )
    assert res.status_code == 400


def test_malformed_session_id_returns_400(client):
    """— strict regex rejects header injection / overlong values."""
    init_db()
    uid = seed_user()
    _, raw = tokens_repo.create(uid, "k")
    h = _handshake(client, raw)
    h["X-Agentwiki-Session"] = "as_x\ninjected"
    res = client.post(
        "/api/mcp",
        json={"jsonrpc": "2.0", "id": 99, "method": "ping"},
        headers=h,
    )
    # FastAPI / Starlette may itself reject newlines in headers; either
    # way we don't want 200.
    assert res.status_code in (400, 422, 500)


def test_session_id_not_starting_with_prefix_returns_400(client):
    init_db()
    uid = seed_user()
    _, raw = tokens_repo.create(uid, "k")
    h = _handshake(client, raw)
    h["X-Agentwiki-Session"] = "no_prefix_here"
    res = client.post(
        "/api/mcp",
        json={"jsonrpc": "2.0", "id": 99, "method": "ping"},
        headers=h,
    )
    assert res.status_code == 400


def test_cross_user_session_returns_403(client):
    """/ audit fix — bearer holder cannot stamp another user's session."""
    init_db()
    a = seed_user("usr_a", email="a@x.com")
    b = seed_user("usr_b", email="b@x.com")
    _, raw_b = tokens_repo.create(b, "k")
    sid_a = sessions_repo.create(
        user_id=a,
        tool_id="claude-code",
        first_turn_prompt="x",
        wiki_path=None,
        working_dir=None,
    )
    h = _handshake(client, raw_b)
    h["X-Agentwiki-Session"] = sid_a
    res = client.post(
        "/api/mcp",
        json={"jsonrpc": "2.0", "id": 99, "method": "ping"},
        headers=h,
    )
    assert res.status_code == 403


def test_no_header_means_null_stamp(client):
    """Backward compat — absent header = no stamp, existing chat-agent
    flow unaffected."""
    init_db()
    uid = seed_user()
    _, raw = tokens_repo.create(uid, "k")
    wiki_git.commit_file("y.md", "# Hello\n", message="seed")
    h = _handshake(client, raw)
    # No X-Agentwiki-Session header.

    client.post(
        "/api/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 99,
            "method": "tools/call",
            "params": {"name": "read_doc", "arguments": {"path": "y.md"}},
        },
        headers=h,
    )

    with session() as s:
        rows = s.scalars(select(AgentActivity)).all()
    matched = [a for a in rows if a.doc_path == "y.md"]
    assert len(matched) == 1
    assert matched[0].agent_session_id is None
