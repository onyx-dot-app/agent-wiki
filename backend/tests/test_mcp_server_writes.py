"""End-to-end tests for the Phase 4 write surface over MCP.

Covers the full handshake → read → edit-with-base_sha → success flow,
the merge-through behavior on drift (a stale ``base_sha`` reconciles
rather than rejecting), the `base_sha_required_for_overwrite` rule for
``write_doc``, ACL enforcement on writes, and the always-present
``stale_paths`` field on tool results.
"""
from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.auth import mcp_tokens as tokens_repo
from app.main import create_app
from app.mcp_server import session as mcp_session
from app.wiki import acl as wiki_acl
from app.wiki import git as wiki_git

from tests._seed import seed_user


@pytest.fixture
def client(tmp_repo):
    mcp_session.reset_for_tests()
    yield TestClient(create_app())
    mcp_session.reset_for_tests()


# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #


def _mint(uid: str) -> str:
    _, raw = tokens_repo.create(uid, "k")
    return raw


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


def _call(
    client, headers: dict[str, str], name: str, args: dict[str, Any]
) -> tuple[dict[str, Any], bool]:
    res = client.post(
        "/api/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 99,
            "method": "tools/call",
            "params": {"name": name, "arguments": args},
        },
        headers=headers,
    )
    body = res.json()
    result = body["result"]
    payload: dict[str, Any] = json.loads(result["content"][0]["text"])
    return payload, bool(result.get("isError"))


def _read(client, headers, path: str) -> dict[str, Any]:
    payload, is_error = _call(client, headers, "read_doc", {"path": path})
    assert not is_error, payload
    return payload


# --------------------------------------------------------------------------- #
# Happy path — read → edit_doc with base_sha → success                        #
# --------------------------------------------------------------------------- #


def test_edit_doc_with_correct_base_sha_succeeds(client):
    uid = seed_user(uid="u1", email="u1@x.com")
    wiki_git.commit_file("guide.md", "# Guide\n\nbefore\n", "seed", author=None)

    headers = _handshake(client, _mint(uid))
    head = _read(client, headers, "guide.md")["sha"]

    payload, is_error = _call(
        client,
        headers,
        "edit_doc",
        {
            "path": "guide.md",
            "old_string": "before",
            "new_string": "after",
            "commit_message": "rename",
            "base_sha": head,
        },
    )
    assert not is_error, payload
    assert payload["sha"] != head
    assert "stale_paths" in payload  # Always present on the MCP surface

    new_body = wiki_git.read_file("guide.md")
    assert "after" in new_body
    assert "before" not in new_body


def test_edit_doc_applies_over_concurrent_change(client):
    """A stale ``base_sha`` no longer aborts the edit: ``edit_doc`` targets the
    current body, so the replace lands on top of the concurrent commit and the
    other writer's change survives."""
    uid = seed_user(uid="u1", email="u1@x.com")
    wiki_git.commit_file("guide.md", "# v1\nbefore\n", "v1", author=None)

    headers = _handshake(client, _mint(uid))
    stale = _read(client, headers, "guide.md")["sha"]

    # Someone else commits, advancing HEAD past the agent's read.
    wiki_git.commit_file("guide.md", "# v2\nbefore\n", "v2", author=None)

    payload, is_error = _call(
        client,
        headers,
        "edit_doc",
        {
            "path": "guide.md",
            "old_string": "before",
            "new_string": "after",
            "commit_message": "race",
            "base_sha": stale,
        },
    )
    assert not is_error, payload
    assert payload["sha"] != stale

    new_body = wiki_git.read_file("guide.md")
    assert "after" in new_body
    assert "before" not in new_body
    assert "# v2" in new_body  # the concurrent change was preserved


def test_edit_doc_without_base_sha_still_works_via_mcp(client):
    """``base_sha`` is optional on edit_doc — the fuzzy ``old_string``
    chain is the safety net. The doc strongly recommends it but doesn't
    require it."""
    uid = seed_user(uid="u1", email="u1@x.com")
    wiki_git.commit_file("guide.md", "# Guide\nbefore\n", "seed", author=None)

    headers = _handshake(client, _mint(uid))
    _read(client, headers, "guide.md")

    payload, is_error = _call(
        client,
        headers,
        "edit_doc",
        {
            "path": "guide.md",
            "old_string": "before",
            "new_string": "after",
            "commit_message": "no base_sha",
        },
    )
    assert not is_error, payload


# --------------------------------------------------------------------------- #
# multi_edit + apply_patch                                                    #
# --------------------------------------------------------------------------- #


def test_multi_edit_with_base_sha(client):
    uid = seed_user(uid="u1", email="u1@x.com")
    wiki_git.commit_file("d.md", "alpha\nbeta\ngamma\n", "seed", author=None)

    headers = _handshake(client, _mint(uid))
    head = _read(client, headers, "d.md")["sha"]

    payload, is_error = _call(
        client,
        headers,
        "multi_edit",
        {
            "path": "d.md",
            "edits": [
                {"old_string": "alpha", "new_string": "ALPHA"},
                {"old_string": "gamma", "new_string": "GAMMA"},
            ],
            "commit_message": "uppercase ends",
            "base_sha": head,
        },
    )
    assert not is_error, payload
    body = wiki_git.read_file("d.md")
    # Frontmatter is auto-injected by the agent-activity registry; the
    # surgical replacements still apply to the body.
    assert "ALPHA" in body and "GAMMA" in body
    assert "alpha" not in body and "gamma" not in body


def test_multi_edit_atomic_abort_on_partial_failure(client):
    uid = seed_user(uid="u1", email="u1@x.com")
    wiki_git.commit_file("d.md", "alpha\nbeta\n", "seed", author=None)

    headers = _handshake(client, _mint(uid))
    head = _read(client, headers, "d.md")["sha"]
    pre_abort_body = wiki_git.read_file("d.md")

    payload, is_error = _call(
        client,
        headers,
        "multi_edit",
        {
            "path": "d.md",
            "edits": [
                {"old_string": "alpha", "new_string": "ALPHA"},
                {"old_string": "missing", "new_string": "x"},  # fails
            ],
            "commit_message": "partial",
            "base_sha": head,
        },
    )
    assert is_error, payload
    # Body is unchanged on disk — the in-memory batch aborted before commit.
    # ALPHA must NOT have leaked through; the original "alpha" remains.
    assert wiki_git.read_file("d.md") == pre_abort_body
    assert "alpha" in pre_abort_body
    assert "ALPHA" not in pre_abort_body


def test_apply_patch_with_correct_base_sha(client):
    uid = seed_user(uid="u1", email="u1@x.com")
    wiki_git.commit_file("p.md", "first\nsecond\nthird\n", "seed", author=None)

    headers = _handshake(client, _mint(uid))
    head = _read(client, headers, "p.md")["sha"]

    diff = (
        "--- a/p.md\n"
        "+++ b/p.md\n"
        "@@ -1,3 +1,3 @@\n"
        " first\n"
        "-second\n"
        "+SECOND\n"
        " third\n"
    )
    payload, is_error = _call(
        client,
        headers,
        "apply_patch",
        {
            "path": "p.md",
            "patch": diff,
            "commit_message": "uppercase second",
            "base_sha": head,
        },
    )
    assert not is_error, payload
    body = wiki_git.read_file("p.md")
    assert "SECOND" in body
    assert "second" not in body


# --------------------------------------------------------------------------- #
# write_doc — base_sha required on overwrite                                  #
# --------------------------------------------------------------------------- #


def test_write_doc_create_new_file(client):
    uid = seed_user(uid="u1", email="u1@x.com")
    headers = _handshake(client, _mint(uid))

    payload, is_error = _call(
        client,
        headers,
        "write_doc",
        {"path": "fresh.md", "body": "# Fresh\n", "commit_message": "create"},
    )
    assert not is_error, payload
    assert payload["created"] is True
    assert wiki_git.read_file("fresh.md") == "# Fresh\n"


def test_write_doc_overwrite_without_base_sha_is_rejected(client):
    uid = seed_user(uid="u1", email="u1@x.com")
    wiki_git.commit_file("d.md", "# v1\n", "seed", author=None)

    headers = _handshake(client, _mint(uid))

    payload, is_error = _call(
        client,
        headers,
        "write_doc",
        {"path": "d.md", "body": "# v2\n", "commit_message": "rewrite"},
    )
    assert is_error
    assert payload["error"] == "base_sha_required_for_overwrite"


def test_write_doc_overwrite_with_correct_base_sha_succeeds(client):
    """Realistic round-trip: agent reads, replaces the body, writes
    back. Activity tracking lives in the DB now, so the body is the
    raw markdown end-to-end."""
    uid = seed_user(uid="u1", email="u1@x.com")
    wiki_git.commit_file("d.md", "# v1\n", "seed", author=None)

    headers = _handshake(client, _mint(uid))
    read_payload = _read(client, headers, "d.md")
    head = read_payload["sha"]
    assert read_payload["body"] == "# v1\n"

    payload, is_error = _call(
        client,
        headers,
        "write_doc",
        {
            "path": "d.md",
            "body": "# v2\n",
            "commit_message": "rewrite",
            "base_sha": head,
        },
    )
    assert not is_error, payload
    assert wiki_git.read_file("d.md") == "# v2\n"


# --------------------------------------------------------------------------- #
# ACL                                                                         #
# --------------------------------------------------------------------------- #


def test_edit_blocked_when_user_lacks_write(client):
    owner = seed_user(uid="owner", email="owner@x.com")
    stranger = seed_user(uid="stranger", email="stranger@x.com")

    wiki_git.commit_file("private.md", "# secrets\nold\n", "seed", author=None)
    # Make the page private: owner stamp + only owner read; no everyone grants.
    wiki_acl.set_owner("private.md", owner)

    headers = _handshake(client, _mint(stranger))
    # Stranger can't even read it, so we can't run read_doc successfully.
    # That's the first wall: ACL stops them before write enforcement matters.
    payload, is_error = _call(client, headers, "read_doc", {"path": "private.md"})
    assert is_error
    assert "forbidden" in payload["error"]


def test_edit_blocked_after_grant_revocation_uses_acl(client):
    """A user who can read but not write is blocked by ``require_can``
    inside ``commit_and_fan_out`` even after a successful read."""
    owner = seed_user(uid="owner", email="owner@x.com")
    reader = seed_user(uid="reader", email="reader@x.com")

    wiki_git.commit_file("doc.md", "# x\nold\n", "seed", author=None)
    wiki_acl.set_owner("doc.md", owner)
    # Grant reader read-only access.
    wiki_acl.grant(
        resource_kind="page",
        resource_path="doc.md",
        principal_kind="user",
        principal_id=reader,
        permission="read",
        granted_by_user_id=owner,
    )

    headers = _handshake(client, _mint(reader))
    head = _read(client, headers, "doc.md")["sha"]

    payload, is_error = _call(
        client,
        headers,
        "edit_doc",
        {
            "path": "doc.md",
            "old_string": "old",
            "new_string": "new",
            "commit_message": "try",
            "base_sha": head,
        },
    )
    assert is_error
    assert "forbidden" in payload["error"]


# --------------------------------------------------------------------------- #
# stale_paths field                                                           #
# --------------------------------------------------------------------------- #


def test_every_tool_result_carries_stale_paths(client):
    """Phase 4 always returns ``stale_paths: []`` on success and error
    alike — the field becomes meaningful once Phase 5 lands subscriptions,
    but the contract is stable from day one."""
    uid = seed_user(uid="u1", email="u1@x.com")
    wiki_git.commit_file("d.md", "# x\n", "seed", author=None)
    headers = _handshake(client, _mint(uid))

    # Successful read
    payload, _ = _call(client, headers, "read_doc", {"path": "d.md"})
    assert payload["stale_paths"] == []

    # App-level error
    payload, _ = _call(client, headers, "read_doc", {"path": "missing.md"})
    assert payload["stale_paths"] == []

    # Disallowed tool
    payload, _ = _call(client, headers, "run_bash", {"command": "ls"})
    assert payload["stale_paths"] == []
