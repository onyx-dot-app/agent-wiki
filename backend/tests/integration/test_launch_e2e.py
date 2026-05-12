"""End-to-end: launch → exchange → fake MCP call → activity stamped.

No real CLI spawn. The "helper" is a second TestClient that POSTs
``/api/launch/exchange`` with the launch code, then opens an MCP
session using the returned bearer + ``X-Agentwiki-Session`` header.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db.models import AgentActivity
from app.db.session import init_db, session
from app.main import create_app
from app.mcp_server import session as mcp_session
from app.wiki import git as wiki_git

from tests._auth import login_fastapi
from tests._seed import seed_user


@pytest.fixture
def client(tmp_repo):
    mcp_session.reset_for_tests()
    init_db()
    yield TestClient(create_app())
    mcp_session.reset_for_tests()


def test_full_launch_flow(client):
    """User clicks Run Agent → helper exchanges → claude makes MCP call
    → activity row stamped with agent_session_id + agent_name."""
    uid = seed_user()
    login_fastapi(client, uid)
    wiki_git.commit_file("x.md", "# Hello\n\nbody.\n", message="seed")

    # 1. POST /api/launch (browser-driven).
    res = client.post(
        "/api/launch",
        json={"tool_id": "claude-code", "wiki_path": "x.md", "message": "go"},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    code = body["launch_code"]
    agent_sid = body["agent_session_id"]

    # 2. POST /api/launch/exchange (helper-style, no cookie).
    fresh = TestClient(create_app())
    ex = fresh.post("/api/launch/exchange", json={"code": code, "machine_id": "m_e2e"})
    assert ex.status_code == 200, ex.text
    exb = ex.json()
    raw_token = exb["mcp_token"]
    assert raw_token.startswith("mcp_")
    assert "Hello" in exb["payload"]["first_turn_prompt"]

    # 3. Helper opens MCP session.
    auth = {"Authorization": f"Bearer {raw_token}"}
    init_res = fresh.post(
        "/api/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": "2025-03-26"},
        },
        headers=auth,
    )
    sess_id = init_res.headers["Mcp-Session-Id"]
    headers = {
        **auth,
        "Mcp-Session-Id": sess_id,
        "X-Agentwiki-Session": agent_sid,
    }
    fresh.post(
        "/api/mcp",
        json={"jsonrpc": "2.0", "method": "notifications/initialized"},
        headers=headers,
    )

    # 4. Tool call → stamps activity.
    call = fresh.post(
        "/api/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "read_doc", "arguments": {"path": "x.md"}},
        },
        headers=headers,
    )
    assert call.status_code == 200, call.text

    # 5. Verify activity row landed with launcher attribution.
    with session() as s:
        rows = s.scalars(select(AgentActivity)).all()
    matched = [a for a in rows if a.agent_session_id == agent_sid and a.doc_path == "x.md"]
    assert len(matched) == 1
    assert matched[0].agent_name == "claude-code"

    # 6. Helper sends spawn-ok beacon.
    spawn_ok = fresh.post(f"/api/agent-sessions/{agent_sid}/spawn-ok", headers=auth)
    assert spawn_ok.status_code == 204

    # 7. List sessions for the page — confirm it shows up.
    sessions_res = client.get(f"/api/agent-sessions?wiki_path=x.md")
    assert sessions_res.status_code == 200
    listed = sessions_res.json()["sessions"]
    assert any(s["id"] == agent_sid for s in listed)
