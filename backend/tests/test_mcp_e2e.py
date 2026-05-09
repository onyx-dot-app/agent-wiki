"""End-to-end smoke test: a fresh user mints a token via the cookie-
authenticated UI, then uses it as a bearer to drive the MCP transport
through a complete read → edit → re-read round-trip.

Exercises the full request lifecycle with no internal short-circuits.
If this passes, an external agent (Claude Code, Cursor) wired to the
same endpoint will work the same way.
"""
from __future__ import annotations

import json
from typing import Any

import pytest
from flask import Flask, jsonify

from app.api import mcp_server as mcp_server_api
from app.api import mcp_tokens as mcp_tokens_api
from app.auth import PermissionDenied
from app.mcp_server import session as mcp_session
from app.models._helpers import ErrorResponse, RequestError
from app.wiki import git as wiki_git

from tests._seed import seed_user


@pytest.fixture
def app(tmp_repo):
    """Wire only the surface an external client touches — token CRUD
    (cookie auth, the same as the Agents page hits) plus the MCP
    transport (bearer auth)."""
    app = Flask(__name__)
    app.config.update(SECRET_KEY="test-secret", TESTING=True)
    app.register_blueprint(mcp_tokens_api.bp, url_prefix="/api/mcp/tokens")
    app.register_blueprint(mcp_server_api.bp, url_prefix="/api/mcp")

    @app.errorhandler(RequestError)
    def _request_error(err: RequestError):  # type: ignore[unused-ignore]
        return jsonify(ErrorResponse(error=err.message).model_dump()), err.status

    @app.errorhandler(PermissionDenied)
    def _permission_denied(err: PermissionDenied):  # type: ignore[unused-ignore]
        return jsonify(ErrorResponse(error=err.message).model_dump()), 403

    mcp_session.reset_for_tests()
    yield app
    mcp_session.reset_for_tests()


@pytest.fixture
def cookie_client(app):
    """A test client representing the user's browser — carries the
    Flask session cookie used by the Agents page to mint tokens."""
    return app.test_client()


@pytest.fixture
def bearer_client(app):
    """A *separate* test client representing the external coding agent.
    No cookie — auth is exclusively via the bearer header set on each
    request."""
    return app.test_client()


def _login(client, uid: str) -> None:
    with client.session_transaction() as sess:
        sess["user_id"] = uid


def _mcp_post(
    client, body: dict[str, Any], *, token: str, session_id: str | None = None
) -> tuple[int, dict[str, Any] | None, dict[str, str]]:
    headers = {"Authorization": f"Bearer {token}"}
    if session_id:
        headers["Mcp-Session-Id"] = session_id
    res = client.post("/api/mcp", json=body, headers=headers)
    payload = res.get_json() if res.data else None
    return res.status_code, payload, dict(res.headers)


def test_full_lifecycle_browser_mints_then_external_agent_uses(
    cookie_client, bearer_client
):
    """User flow:
      1. Browser logs in (cookie).
      2. Browser POSTs /api/mcp/tokens to mint a key. Sees the key once.
      3. External agent uses the key as a bearer:
         - initialize → get session id
         - notifications/initialized → ack
         - tools/list → see the wiki tool surface
         - tools/call read_doc on a seeded doc
         - tools/call edit_doc with base_sha → sha advances
         - tools/call read_doc again → new body visible
      4. Browser POSTs DELETE to revoke the key.
      5. External agent's next call now fails 401 (bearer is dead).
    """
    uid = seed_user(uid="u_alice", email="alice@x.com", name="Alice")
    wiki_git.commit_file("notes.md", "# Notes\n\nbefore the change\n", "seed", author=None)

    # ── 1 + 2 — browser session mints a token ──────────────────────
    _login(cookie_client, uid)
    res = cookie_client.post("/api/mcp/tokens", json={"name": "claude-code"})
    assert res.status_code == 201
    minted = res.get_json()
    raw = minted["token"]
    assert raw.startswith("mcp_")

    # ── 3 — external agent uses the bearer ─────────────────────────
    status, body, headers = _mcp_post(
        bearer_client,
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": "2025-03-26"},
        },
        token=raw,
    )
    assert status == 200
    assert body and body["result"]["serverInfo"]["name"] == "agent-wiki"
    sess_id = headers["Mcp-Session-Id"]
    assert sess_id.startswith("mcps_")

    status, _, _ = _mcp_post(
        bearer_client,
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        token=raw,
        session_id=sess_id,
    )
    assert status == 202

    status, body, _ = _mcp_post(
        bearer_client,
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        token=raw,
        session_id=sess_id,
    )
    assert status == 200 and body
    names = {t["name"] for t in body["result"]["tools"]}
    assert {"read_doc", "edit_doc", "write_doc", "search_wiki"} <= names

    # read_doc to satisfy read-before-write and grab a base_sha.
    status, body, _ = _mcp_post(
        bearer_client,
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "read_doc", "arguments": {"path": "notes.md"}},
        },
        token=raw,
        session_id=sess_id,
    )
    assert status == 200 and body
    read_payload = json.loads(body["result"]["content"][0]["text"])
    assert read_payload["is_head"] is True
    assert "before the change" in read_payload["body"]
    head = read_payload["sha"]
    assert read_payload["stale_paths"] == []

    # edit_doc with the base_sha we just got.
    status, body, _ = _mcp_post(
        bearer_client,
        {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {
                "name": "edit_doc",
                "arguments": {
                    "path": "notes.md",
                    "old_string": "before the change",
                    "new_string": "after the change",
                    "commit_message": "rename via mcp e2e",
                    "base_sha": head,
                },
            },
        },
        token=raw,
        session_id=sess_id,
    )
    assert status == 200 and body
    edit_payload = json.loads(body["result"]["content"][0]["text"])
    assert "error" not in edit_payload, edit_payload
    new_sha = edit_payload["sha"]
    assert new_sha != head

    # read again — new body visible.
    status, body, _ = _mcp_post(
        bearer_client,
        {
            "jsonrpc": "2.0",
            "id": 5,
            "method": "tools/call",
            "params": {"name": "read_doc", "arguments": {"path": "notes.md"}},
        },
        token=raw,
        session_id=sess_id,
    )
    assert body
    second_read = json.loads(body["result"]["content"][0]["text"])
    assert "after the change" in second_read["body"]
    assert "before the change" not in second_read["body"]

    # ── 4 — browser revokes the key ────────────────────────────────
    res = cookie_client.delete(f"/api/mcp/tokens/{minted['id']}")
    assert res.status_code == 204

    # ── 5 — bearer is now dead ─────────────────────────────────────
    status, _, _ = _mcp_post(
        bearer_client,
        {"jsonrpc": "2.0", "id": 99, "method": "ping"},
        token=raw,
        session_id=sess_id,
    )
    assert status == 401


def test_listing_my_tokens_post_revoke_is_empty(cookie_client):
    uid = seed_user(uid="u_alice", email="alice@x.com")
    _login(cookie_client, uid)

    minted = cookie_client.post("/api/mcp/tokens", json={"name": "k1"}).get_json()
    cookie_client.post("/api/mcp/tokens", json={"name": "k2"})

    listing = cookie_client.get("/api/mcp/tokens").get_json()
    assert {t["name"] for t in listing["tokens"]} == {"k1", "k2"}

    cookie_client.delete(f"/api/mcp/tokens/{minted['id']}")
    listing = cookie_client.get("/api/mcp/tokens").get_json()
    assert {t["name"] for t in listing["tokens"]} == {"k2"}


def test_two_users_tokens_are_isolated_end_to_end(cookie_client, bearer_client):
    """Alice's token can't read Bob's private doc, even via a fully
    initialized MCP session."""
    from app.wiki import acl as wiki_acl

    alice = seed_user(uid="u_alice", email="alice@x.com")
    bob = seed_user(uid="u_bob", email="bob@x.com")

    # Bob commits a private doc.
    wiki_git.commit_file("bobs_secret.md", "# Bobs\nshh\n", "seed", author=None)
    wiki_acl.set_owner("bobs_secret.md", bob)

    # Alice mints a token via her browser session.
    _login(cookie_client, alice)
    raw = cookie_client.post("/api/mcp/tokens", json={"name": "k"}).get_json()["token"]

    # Alice's external agent initializes, then tries to read Bob's doc.
    _, _, headers = _mcp_post(
        bearer_client,
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": "2025-03-26"},
        },
        token=raw,
    )
    sess_id = headers["Mcp-Session-Id"]
    _mcp_post(
        bearer_client,
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        token=raw,
        session_id=sess_id,
    )

    _, body, _ = _mcp_post(
        bearer_client,
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "read_doc",
                "arguments": {"path": "bobs_secret.md"},
            },
        },
        token=raw,
        session_id=sess_id,
    )
    assert body
    payload = json.loads(body["result"]["content"][0]["text"])
    assert "forbidden" in payload["error"]
