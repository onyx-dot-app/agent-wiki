"""Tests for the read-only MCP tool surface (Phase 3).

Verifies ``tools/list`` exposes the right allow-list with MCP-shape
field names, and ``tools/call`` dispatches into the existing
chat-agent handlers — including ACL enforcement and historical
(sha-pinned) reads.
"""
from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.auth import mcp_tokens as tokens_repo
from app.main import create_app
from app.mcp_server import session as mcp_session
from app.mcp_server import tools as mcp_tools
from app.wiki import acl as wiki_acl
from tests.conftest import needs_opensearch
from app.wiki import comments as wiki_comments
from app.wiki import git as wiki_git

from tests._seed import seed_user


@pytest.fixture
def client(tmp_repo):
    mcp_session.reset_for_tests()
    yield TestClient(create_app())
    mcp_session.reset_for_tests()


def _mint(uid: str) -> str:
    _, raw = tokens_repo.create(uid, "k")
    return raw


def _handshake(client, raw: str) -> dict[str, str]:
    """Run initialize + notifications/initialized; return headers carrying
    the session id so subsequent requests reuse it."""
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


def _call_tool(
    client, headers: dict[str, str], name: str, arguments: dict[str, Any], req_id: int = 100
) -> dict[str, Any]:
    res = client.post(
        "/api/mcp",
        json={
            "jsonrpc": "2.0",
            "id": req_id,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        },
        headers=headers,
    )
    return res.json()


def _payload_from_call_response(rpc: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """Pull the JSON-stringified payload out of MCP's content envelope."""
    result = rpc["result"]
    assert isinstance(result, dict)
    is_error = bool(result.get("isError"))
    content = result["content"]
    assert isinstance(content, list) and len(content) == 1
    block = content[0]
    assert isinstance(block, dict)
    payload: dict[str, Any] = json.loads(block["text"])
    return payload, is_error


# --------------------------------------------------------------------------- #
# tools/list                                                                  #
# --------------------------------------------------------------------------- #


def test_tools_list_uses_mcp_shape_and_allow_list(client):
    uid = seed_user(uid="u1", email="u1@x.com")
    headers = _handshake(client, _mint(uid))

    res = client.post(
        "/api/mcp",
        json={"jsonrpc": "2.0", "id": 5, "method": "tools/list"},
        headers=headers,
    )
    body = res.json()
    tools = body["result"]["tools"]

    names = {t["name"] for t in tools}
    # Phase 3 + 4 surface — read tools and the sync write tools.
    assert names >= {
        "read_doc",
        "search_wiki",
        "search_comments",
        "list_history",
        "ask_nl_question",
        "edit_doc",
        "multi_edit",
        "write_doc",
        "apply_patch",
        "move_path",
        "create_directory",
        "add_comment",
        "reply_comment",
        "resolve_comment",
    }
    # Tools not on the allow-list must NOT be exposed.
    assert "run_bash" not in names
    assert "web_search" not in names
    # Phase 6 added the async update_doc_nl tool.
    assert "update_doc_nl" in names

    # MCP shape: inputSchema (camelCase), not input_schema.
    for t in tools:
        assert "inputSchema" in t
        assert "input_schema" not in t
        assert isinstance(t["description"], str)


def test_tools_list_matches_module_allow_list(client):
    """Sanity check: ``list_for_mcp()`` and ``MCP_ALLOWED_TOOLS`` agree."""
    uid = seed_user(uid="u1", email="u1@x.com")
    headers = _handshake(client, _mint(uid))

    res = client.post(
        "/api/mcp",
        json={"jsonrpc": "2.0", "id": 5, "method": "tools/list"},
        headers=headers,
    )
    listed = {t["name"] for t in res.json()["result"]["tools"]}
    assert listed == set(mcp_tools.MCP_ALLOWED_TOOLS)


# --------------------------------------------------------------------------- #
# read_doc — HEAD + historical                                                #
# --------------------------------------------------------------------------- #


def test_read_doc_head(client):
    uid = seed_user(uid="u1", email="u1@x.com")
    wiki_git.commit_file("guide.md", "# Guide\n\nFirst.\n", "seed", author=None)

    headers = _handshake(client, _mint(uid))
    rpc = _call_tool(client, headers, "read_doc", {"path": "guide.md"})
    payload, is_error = _payload_from_call_response(rpc)

    assert is_error is False
    assert payload["path"] == "guide.md"
    assert "First." in payload["body"]
    assert payload["is_head"] is True
    assert payload["sha"]


def test_read_doc_at_sha(client):
    uid = seed_user(uid="u1", email="u1@x.com")
    first = wiki_git.commit_file("page.md", "# v1\n", "v1", author=None)
    wiki_git.commit_file("page.md", "# v2\n", "v2", author=None)

    headers = _handshake(client, _mint(uid))
    rpc = _call_tool(client, headers, "read_doc", {"path": "page.md", "sha": first})
    payload, is_error = _payload_from_call_response(rpc)

    assert is_error is False
    assert payload["body"].startswith("# v1")
    assert payload["is_head"] is False
    assert payload["sha"] == first


def test_read_doc_unknown_sha_returns_error(client):
    uid = seed_user(uid="u1", email="u1@x.com")
    wiki_git.commit_file("page.md", "# v1\n", "v1", author=None)

    headers = _handshake(client, _mint(uid))
    rpc = _call_tool(
        client, headers, "read_doc", {"path": "page.md", "sha": "deadbeef00000000"}
    )
    payload, is_error = _payload_from_call_response(rpc)
    assert is_error is True
    assert "sha_not_found" in payload["error"]


# --------------------------------------------------------------------------- #
# ACL enforcement                                                             #
# --------------------------------------------------------------------------- #


def test_read_doc_blocked_when_user_lacks_read(client):
    """A page with no public grants is invisible to a non-owner."""
    owner = seed_user(uid="owner", email="owner@x.com")
    stranger = seed_user(uid="stranger", email="stranger@x.com")

    # Commit, then explicitly seed a private ACL: owner stamp + no
    # everyone grants.
    wiki_git.commit_file("private.md", "# secrets\n", "seed", author=None)
    wiki_acl.set_owner("private.md", owner)
    # No grants — only the owner (and admins) can read.

    headers = _handshake(client, _mint(stranger))
    rpc = _call_tool(client, headers, "read_doc", {"path": "private.md"})
    payload, is_error = _payload_from_call_response(rpc)
    assert is_error is True
    assert "forbidden" in payload["error"]


def test_list_history_blocked_when_user_lacks_read(client):
    owner = seed_user(uid="owner", email="owner@x.com")
    stranger = seed_user(uid="stranger", email="stranger@x.com")

    wiki_git.commit_file("private.md", "# secrets\n", "seed", author=None)
    wiki_acl.set_owner("private.md", owner)

    headers = _handshake(client, _mint(stranger))
    rpc = _call_tool(client, headers, "list_history", {"path": "private.md"})
    payload, is_error = _payload_from_call_response(rpc)
    assert is_error is True
    assert "forbidden" in payload["error"]


def test_add_comment_blocked_when_user_lacks_read(client):
    """A stranger can't comment on a page they can't read — `add_comment`
    gates on `require_can("read", path)` before it ever anchors."""
    owner = seed_user(uid="owner", email="owner@x.com")
    stranger = seed_user(uid="stranger", email="stranger@x.com")

    wiki_git.commit_file("private.md", "# secrets\nthe secret value\n", "seed", author=None)
    wiki_acl.set_owner("private.md", owner)

    headers = _handshake(client, _mint(stranger))
    rpc = _call_tool(
        client,
        headers,
        "add_comment",
        {"path": "private.md", "quoted_text": "the secret value", "body": "x"},
    )
    payload, is_error = _payload_from_call_response(rpc)
    assert is_error is True
    assert "forbidden" in payload["error"]
    assert wiki_comments.list_for_doc("private.md") == []  # nothing created


# --------------------------------------------------------------------------- #
# Disallowed / unknown tools                                                  #
# --------------------------------------------------------------------------- #


def test_call_to_disallowed_tool_is_error(client):
    """``run_bash`` exists in the chat-agent registry but is deliberately
    excluded from the MCP allow-list (out of scope for v0)."""
    uid = seed_user(uid="u1", email="u1@x.com")
    headers = _handshake(client, _mint(uid))
    rpc = _call_tool(client, headers, "run_bash", {"command": "ls"})
    payload, is_error = _payload_from_call_response(rpc)
    assert is_error is True
    assert "unknown tool" in payload["error"]


def test_call_to_nonexistent_tool_is_error(client):
    uid = seed_user(uid="u1", email="u1@x.com")
    headers = _handshake(client, _mint(uid))
    rpc = _call_tool(client, headers, "totally_made_up_tool", {})
    _, is_error = _payload_from_call_response(rpc)
    assert is_error is True


def test_call_with_missing_name_is_invalid_params(client):
    uid = seed_user(uid="u1", email="u1@x.com")
    headers = _handshake(client, _mint(uid))
    res = client.post(
        "/api/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 9,
            "method": "tools/call",
            "params": {"arguments": {}},
        },
        headers=headers,
    )
    body = res.json()
    # JSON-RPC "Invalid params" — protocol-level, not result-level.
    assert body["error"]["code"] == -32602


# --------------------------------------------------------------------------- #
# Comment write tools (add / reply / resolve)                                 #
# --------------------------------------------------------------------------- #


def test_add_comment_via_mcp(client):
    uid = seed_user(uid="u1", email="u1@x.com")
    wiki_git.commit_file(
        "guide.md", "# Guide\nThe pool size is 20.\nEnd.\n", "seed", author=None
    )
    headers = _handshake(client, _mint(uid))

    rpc = _call_tool(
        client,
        headers,
        "add_comment",
        {"path": "guide.md", "quoted_text": "The pool size is 20.", "body": "confirm?"},
    )
    payload, is_error = _payload_from_call_response(rpc)
    assert is_error is False
    assert payload["comment_id"].startswith("cmt_")
    assert payload["doc_path"] == "guide.md"

    rows = wiki_comments.list_for_doc("guide.md")
    assert len(rows) == 1
    # Authored by the agent, attributed to the authenticated MCP user.
    assert rows[0]["author_kind"] == "agent"
    assert rows[0]["author_user_id"] == uid
    assert rows[0]["quoted_text"] == "The pool size is 20."


def test_reply_comment_via_mcp(client):
    uid = seed_user(uid="u1", email="u1@x.com")
    wiki_git.commit_file("guide.md", "# Guide\nbody\n", "seed", author=None)
    root = wiki_comments.create_thread(
        doc_path="guide.md",
        body="is this current?",
        author_user_id=uid,
        anchor_sha="seed",
        start_offset=0,
        end_offset=4,
        quoted_text="Guid",
    )
    headers = _handshake(client, _mint(uid))

    rpc = _call_tool(
        client, headers, "reply_comment", {"comment_id": root["id"], "body": "yes, confirmed"}
    )
    payload, is_error = _payload_from_call_response(rpc)
    assert is_error is False
    assert payload["thread_root_id"] == root["id"]

    thread = wiki_comments.list_thread(root["id"])
    assert len(thread) == 2
    reply = next(c for c in thread if c["parent_id"])
    assert reply["author_kind"] == "agent"
    assert reply["author_user_id"] == uid


def test_resolve_comment_via_mcp(client):
    uid = seed_user(uid="u1", email="u1@x.com")
    wiki_git.commit_file("guide.md", "# Guide\nbody\n", "seed", author=None)
    root = wiki_comments.create_thread(
        doc_path="guide.md",
        body="needs review",
        author_user_id=uid,
        anchor_sha="seed",
        start_offset=0,
        end_offset=4,
        quoted_text="Guid",
    )
    headers = _handshake(client, _mint(uid))

    rpc = _call_tool(client, headers, "resolve_comment", {"comment_id": root["id"]})
    payload, is_error = _payload_from_call_response(rpc)
    assert is_error is False
    assert payload["status"] == "resolved"

    refreshed = wiki_comments.get(root["id"])
    assert refreshed is not None and refreshed["status"] == "resolved"


# --------------------------------------------------------------------------- #
# Search                                                                      #
# --------------------------------------------------------------------------- #


@needs_opensearch
def test_search_comments_returns_results_via_mcp(client):
    uid = seed_user(uid="u1", email="u1@x.com")

    # A comment indexes inline on create (no queue), so it's searchable at once.
    wiki_git.commit_file("guide.md", "# Guide\nsome text here\n", "seed", author=None)
    wiki_comments.create_thread(
        doc_path="guide.md",
        body="we chose distributed tracing for the rollout",
        author_user_id=uid,
        anchor_sha="seed",
        start_offset=0,
        end_offset=4,
        quoted_text="some",
    )

    headers = _handshake(client, _mint(uid))
    rpc = _call_tool(client, headers, "search_comments", {"query": "distributed tracing"})
    payload, is_error = _payload_from_call_response(rpc)
    assert is_error is False
    paths = [r["doc_path"] for r in payload["results"]]
    assert "guide.md" in paths


@needs_opensearch
def test_search_wiki_returns_results_via_mcp(client):
    uid = seed_user(uid="u1", email="u1@x.com")
    # The reindex task is bound to lightweight_maintenance_queue. Run it
    # inline here so the search index sees the doc the test seeded.
    from app.tasks.queues import lightweight_maintenance_queue
    from app.tasks.reindex import index_path_inline

    with lightweight_maintenance_queue.immediate_mode():
        wiki_git.commit_file("guide.md", "# Distributed search rocks\n", "seed", author=None)
        index_path_inline("guide.md")

    headers = _handshake(client, _mint(uid))
    rpc = _call_tool(client, headers, "search_wiki", {"query": "distributed"})
    payload, is_error = _payload_from_call_response(rpc)
    assert is_error is False
    paths = [r["path"] for r in payload["results"]]
    assert "guide.md" in paths
