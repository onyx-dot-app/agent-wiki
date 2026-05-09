"""Tests for the inbound MCP transport — bearer auth, session
handshake, JSON-RPC dispatch."""
from __future__ import annotations

import pytest
from flask import Flask, g, jsonify

from app.api import mcp_server as mcp_server_api
from app.auth import current_user, mcp_tokens as tokens_repo
from app.auth.mcp_tokens import TOKEN_PREFIX
from app.mcp_server import session as mcp_session
from app.models._helpers import ErrorResponse, RequestError

from tests._seed import seed_user


@pytest.fixture
def client(tmp_db):
    app = Flask(__name__)
    app.config.update(SECRET_KEY="test-secret", TESTING=True)
    app.register_blueprint(mcp_server_api.bp, url_prefix="/api/mcp")

    @app.errorhandler(RequestError)
    def _request_error(err: RequestError):  # type: ignore[unused-ignore]
        return jsonify(ErrorResponse(error=err.message).model_dump()), err.status

    mcp_session.reset_for_tests()
    yield app.test_client()
    mcp_session.reset_for_tests()


def _mint_token(uid: str, name: str = "k") -> str:
    _, raw = tokens_repo.create(uid, name)
    return raw


def _initialize_request(req_id: int = 1) -> dict[str, object]:
    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-03-26",
            "capabilities": {},
            "clientInfo": {"name": "test-client", "version": "0"},
        },
    }


# --------------------------------------------------------------------------- #
# Bearer auth                                                                 #
# --------------------------------------------------------------------------- #


def test_no_auth_header_is_401(client):
    res = client.post("/api/mcp", json=_initialize_request())
    assert res.status_code == 401
    assert res.get_json()["error"]


def test_non_bearer_scheme_is_401(client):
    res = client.post(
        "/api/mcp",
        json=_initialize_request(),
        headers={"Authorization": "Basic abc:def"},
    )
    assert res.status_code == 401


def test_unknown_token_is_401(client):
    seed_user(uid="u1", email="u1@x.com")
    res = client.post(
        "/api/mcp",
        json=_initialize_request(),
        headers={"Authorization": f"Bearer {TOKEN_PREFIX}{'z' * 32}"},
    )
    assert res.status_code == 401


def test_revoked_token_is_401(client):
    uid = seed_user(uid="u1", email="u1@x.com")
    token_id, raw = tokens_repo.create(uid, "k")
    assert tokens_repo.revoke(token_id, uid)

    res = client.post(
        "/api/mcp",
        json=_initialize_request(),
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert res.status_code == 401


# --------------------------------------------------------------------------- #
# initialize handshake                                                        #
# --------------------------------------------------------------------------- #


def test_initialize_returns_capabilities_and_session_id(client):
    uid = seed_user(uid="u1", email="u1@x.com")
    raw = _mint_token(uid)

    res = client.post(
        "/api/mcp",
        json=_initialize_request(req_id=42),
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert res.status_code == 200

    body = res.get_json()
    assert body["jsonrpc"] == "2.0"
    assert body["id"] == 42
    assert "result" in body
    assert body["result"]["protocolVersion"] == "2025-03-26"
    assert body["result"]["serverInfo"]["name"] == "agent-wiki"

    caps = body["result"]["capabilities"]
    assert "tools" in caps
    assert caps["resources"]["subscribe"] is True

    sess_id = res.headers.get("Mcp-Session-Id")
    assert sess_id is not None
    assert sess_id.startswith("mcps_")


def test_initialize_creates_session_for_token_user(client):
    """Side-effect proof that ``g.user`` is wired correctly: the session
    in the registry must be tied to the bearer token's owner. If
    ``bearer_required`` failed to populate ``g.user``,
    ``_handle_initialize`` would crash on the assertion before reaching
    ``mcp_session.create``.
    """
    uid = seed_user(uid="u1", email="u1@x.com")
    raw = _mint_token(uid)

    res = client.post(
        "/api/mcp",
        json=_initialize_request(),
        headers={"Authorization": f"Bearer {raw}"},
    )
    sess_id = res.headers["Mcp-Session-Id"]

    sess = mcp_session.get(sess_id)
    assert sess is not None
    assert sess.user_id == uid
    assert sess.initialized is False  # client must ack via notifications/initialized


# --------------------------------------------------------------------------- #
# Post-initialize flow                                                        #
# --------------------------------------------------------------------------- #


def test_full_handshake_then_tools_list(client):
    uid = seed_user(uid="u1", email="u1@x.com")
    raw = _mint_token(uid)
    auth = {"Authorization": f"Bearer {raw}"}

    res = client.post("/api/mcp", json=_initialize_request(), headers=auth)
    sess_id = res.headers["Mcp-Session-Id"]

    res = client.post(
        "/api/mcp",
        json={"jsonrpc": "2.0", "method": "notifications/initialized"},
        headers={**auth, "Mcp-Session-Id": sess_id},
    )
    assert res.status_code == 202
    assert res.data == b""

    sess = mcp_session.get(sess_id)
    assert sess is not None and sess.initialized is True

    res = client.post(
        "/api/mcp",
        json={"jsonrpc": "2.0", "id": 7, "method": "tools/list"},
        headers={**auth, "Mcp-Session-Id": sess_id},
    )
    body = res.get_json()
    assert body["id"] == 7
    # Phase 3 added the read-only tools; the handshake itself doesn't care
    # which ones — just that the surface is reachable post-initialization.
    assert isinstance(body["result"]["tools"], list)
    assert all("name" in t and "inputSchema" in t for t in body["result"]["tools"])


def test_ping_returns_empty_result(client):
    uid = seed_user(uid="u1", email="u1@x.com")
    raw = _mint_token(uid)
    auth = {"Authorization": f"Bearer {raw}"}

    res = client.post("/api/mcp", json=_initialize_request(), headers=auth)
    sess_id = res.headers["Mcp-Session-Id"]
    client.post(
        "/api/mcp",
        json={"jsonrpc": "2.0", "method": "notifications/initialized"},
        headers={**auth, "Mcp-Session-Id": sess_id},
    )

    res = client.post(
        "/api/mcp",
        json={"jsonrpc": "2.0", "id": 99, "method": "ping"},
        headers={**auth, "Mcp-Session-Id": sess_id},
    )
    assert res.get_json() == {"jsonrpc": "2.0", "id": 99, "result": {}}


# --------------------------------------------------------------------------- #
# Protocol errors                                                             #
# --------------------------------------------------------------------------- #


def test_request_without_session_id_is_protocol_error(client):
    uid = seed_user(uid="u1", email="u1@x.com")
    raw = _mint_token(uid)

    res = client.post(
        "/api/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        headers={"Authorization": f"Bearer {raw}"},
    )
    body = res.get_json()
    assert "error" in body
    # JSON-RPC 2.0 "Invalid Request"
    assert body["error"]["code"] == -32600
    assert "Mcp-Session-Id" in body["error"]["message"]


def test_method_before_initialized_ack_is_error(client):
    uid = seed_user(uid="u1", email="u1@x.com")
    raw = _mint_token(uid)
    auth = {"Authorization": f"Bearer {raw}"}

    res = client.post("/api/mcp", json=_initialize_request(), headers=auth)
    sess_id = res.headers["Mcp-Session-Id"]

    # tools/list without sending notifications/initialized first
    res = client.post(
        "/api/mcp",
        json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        headers={**auth, "Mcp-Session-Id": sess_id},
    )
    body = res.get_json()
    assert body["error"]["code"] == -32600
    assert "not initialized" in body["error"]["message"]


def test_unknown_method_is_method_not_found(client):
    uid = seed_user(uid="u1", email="u1@x.com")
    raw = _mint_token(uid)
    auth = {"Authorization": f"Bearer {raw}"}

    res = client.post("/api/mcp", json=_initialize_request(), headers=auth)
    sess_id = res.headers["Mcp-Session-Id"]
    client.post(
        "/api/mcp",
        json={"jsonrpc": "2.0", "method": "notifications/initialized"},
        headers={**auth, "Mcp-Session-Id": sess_id},
    )

    res = client.post(
        "/api/mcp",
        json={"jsonrpc": "2.0", "id": 3, "method": "nonsense/totally-fake"},
        headers={**auth, "Mcp-Session-Id": sess_id},
    )
    body = res.get_json()
    assert body["error"]["code"] == -32601


def test_missing_jsonrpc_field_is_invalid_request(client):
    uid = seed_user(uid="u1", email="u1@x.com")
    raw = _mint_token(uid)

    res = client.post(
        "/api/mcp",
        json={"id": 1, "method": "initialize", "params": {}},
        headers={"Authorization": f"Bearer {raw}"},
    )
    body = res.get_json()
    assert body["error"]["code"] == -32600


def test_non_dict_body_is_400(client):
    uid = seed_user(uid="u1", email="u1@x.com")
    raw = _mint_token(uid)

    res = client.post(
        "/api/mcp",
        json=["not", "an", "object"],
        headers={"Authorization": f"Bearer {raw}"},
    )
    assert res.status_code == 400


# --------------------------------------------------------------------------- #
# current_user() — explicit verification that g.user works mid-handler        #
# --------------------------------------------------------------------------- #


def test_current_user_visible_during_dispatch(client, monkeypatch):
    """The doc's stated exit criterion: ``current_user()`` inside a tool
    dispatch returns the token's user. Phase 2 has no real tools, so we
    verify the same property via a wrapper around ``dispatch`` — the
    same code path tools will hit in Phase 3."""
    uid = seed_user(uid="u1", email="u1@x.com", name="One")
    raw = _mint_token(uid)

    captured: dict[str, str | None] = {}
    real_dispatch = mcp_server_api.dispatch

    def spy(message, session_id):
        u = current_user()
        captured["uid"] = u.id if u else None
        captured["email"] = u.email if u else None
        # Also assert g.user is set directly — same source `current_user` reads.
        captured["g_user_id"] = getattr(g, "user", None) and g.user.id
        return real_dispatch(message, session_id)

    monkeypatch.setattr(mcp_server_api, "dispatch", spy)

    client.post(
        "/api/mcp",
        json=_initialize_request(),
        headers={"Authorization": f"Bearer {raw}"},
    )

    assert captured["uid"] == uid
    assert captured["email"] == "u1@x.com"
    assert captured["g_user_id"] == uid
