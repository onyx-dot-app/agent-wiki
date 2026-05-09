"""Phase 5 — resource subscriptions, pub-sub fan-out, SSE delivery,
``stale_paths`` field, ACL recheck on revoke.

We exercise the in-process delivery path directly. The Postgres
LISTEN/NOTIFY bridge (cross-process) is not started in tests; same-
process commits all flow through ``pubsub._publish_local`` which is
the path actually wired into ``app.wiki.notify``.
"""
from __future__ import annotations

import json
from typing import Any

import pytest
from flask import Flask, jsonify

from app.api import mcp_server as mcp_server_api
from app.auth import mcp_tokens as tokens_repo
from app.mcp_server import pubsub as mcp_pubsub
from app.mcp_server import session as mcp_session
from app.models._helpers import ErrorResponse, RequestError
from app.wiki import acl as wiki_acl
from app.wiki import git as wiki_git

from tests._seed import seed_user


@pytest.fixture
def client(tmp_repo):
    app = Flask(__name__)
    app.config.update(SECRET_KEY="test-secret", TESTING=True)
    app.register_blueprint(mcp_server_api.bp, url_prefix="/api/mcp")

    @app.errorhandler(RequestError)
    def _request_error(err: RequestError):  # type: ignore[unused-ignore]
        return jsonify(ErrorResponse(error=err.message).model_dump()), err.status

    mcp_session.reset_for_tests()
    yield app.test_client()
    mcp_session.reset_for_tests()


# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #


def _mint(uid: str) -> str:
    _, raw = tokens_repo.create(uid, "k")
    return raw


def _handshake(client, raw: str) -> tuple[dict[str, str], str]:
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
    return {**auth, "Mcp-Session-Id": sess_id}, sess_id


def _rpc(client, headers: dict[str, str], method: str, params: dict[str, Any] | None = None,
         req_id: int = 99) -> dict[str, Any]:
    res = client.post(
        "/api/mcp",
        json={"jsonrpc": "2.0", "id": req_id, "method": method, "params": params or {}},
        headers=headers,
    )
    return res.get_json()


def _tool(client, headers: dict[str, str], name: str, args: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    body = _rpc(
        client,
        headers,
        "tools/call",
        {"name": name, "arguments": args},
    )
    result = body["result"]
    payload: dict[str, Any] = json.loads(result["content"][0]["text"])
    return payload, bool(result.get("isError"))


# --------------------------------------------------------------------------- #
# resources/list                                                              #
# --------------------------------------------------------------------------- #


def test_resources_list_returns_md_files_filtered_by_acl(client):
    owner = seed_user(uid="owner", email="owner@x.com")
    reader = seed_user(uid="reader", email="reader@x.com")

    wiki_git.commit_file("public.md", "# pub\n", "seed", author=None)
    wiki_git.commit_file("secret.md", "# secret\n", "seed", author=None)
    wiki_acl.set_owner("secret.md", owner)
    # secret.md has no everyone grants, so only owner + admin can read.

    headers, _ = _handshake(client, _mint(reader))
    body = _rpc(client, headers, "resources/list")

    uris = {r["uri"] for r in body["result"]["resources"]}
    assert "wiki:///public.md" in uris
    assert "wiki:///secret.md" not in uris


def test_resources_list_admin_sees_everything(client):
    seed_user(uid="admin", email="a@x.com", is_admin=True)
    wiki_git.commit_file("a.md", "x", "seed", author=None)
    wiki_git.commit_file("b.md", "x", "seed", author=None)

    headers, _ = _handshake(client, _mint("admin"))
    body = _rpc(client, headers, "resources/list")
    uris = {r["uri"] for r in body["result"]["resources"]}
    assert "wiki:///a.md" in uris
    assert "wiki:///b.md" in uris


# --------------------------------------------------------------------------- #
# resources/read                                                              #
# --------------------------------------------------------------------------- #


def test_resources_read_returns_body_at_head(client):
    uid = seed_user(uid="u1", email="u1@x.com")
    wiki_git.commit_file("notes.md", "# notes\n\nhello\n", "seed", author=None)

    headers, _ = _handshake(client, _mint(uid))
    body = _rpc(client, headers, "resources/read", {"uri": "wiki:///notes.md"})

    contents = body["result"]["contents"]
    assert len(contents) == 1
    assert contents[0]["uri"] == "wiki:///notes.md"
    assert contents[0]["mimeType"] == "text/markdown"
    assert "hello" in contents[0]["text"]


def test_resources_read_blocked_for_unauthorized_user(client):
    owner = seed_user(uid="owner", email="owner@x.com")
    stranger = seed_user(uid="stranger", email="s@x.com")
    wiki_git.commit_file("private.md", "# p\n", "seed", author=None)
    wiki_acl.set_owner("private.md", owner)

    headers, _ = _handshake(client, _mint(stranger))
    body = _rpc(client, headers, "resources/read", {"uri": "wiki:///private.md"})

    result = body["result"]
    assert result["isError"] is True
    assert "forbidden" in result["error"]


def test_resources_read_unknown_uri_scheme_is_invalid_params(client):
    uid = seed_user(uid="u1", email="u1@x.com")
    headers, _ = _handshake(client, _mint(uid))
    body = _rpc(client, headers, "resources/read", {"uri": "https://example.com/x.md"})
    assert body["error"]["code"] == -32602


# --------------------------------------------------------------------------- #
# resources/subscribe + auto-subscribe via read_doc                           #
# --------------------------------------------------------------------------- #


def test_explicit_subscribe_then_publish_lands_in_session_queue(client):
    uid = seed_user(uid="u1", email="u1@x.com")
    wiki_git.commit_file("page.md", "# x\nbefore\n", "seed", author=None)

    headers, sess_id = _handshake(client, _mint(uid))
    body = _rpc(client, headers, "resources/subscribe", {"uri": "wiki:///page.md"})
    assert body["result"] == {}

    # Direct publish — the wiki/notify hook will call this in production.
    mcp_pubsub.publish_doc_update("page.md", "abc123", "edit")

    notif = mcp_pubsub.queue_for(sess_id).get(timeout=2.0)
    assert notif.method == "notifications/resources/updated"
    assert notif.params["uri"] == "wiki:///page.md"
    assert notif.params["sha"] == "abc123"
    assert notif.params["changeKind"] == "edit"


def test_subscribe_blocked_when_user_lacks_read(client):
    owner = seed_user(uid="owner", email="owner@x.com")
    stranger = seed_user(uid="stranger", email="s@x.com")
    wiki_git.commit_file("private.md", "# p\n", "seed", author=None)
    wiki_acl.set_owner("private.md", owner)

    headers, sess_id = _handshake(client, _mint(stranger))
    body = _rpc(client, headers, "resources/subscribe", {"uri": "wiki:///private.md"})

    assert body["error"]["code"] == -32600
    assert "forbidden" in body["error"]["message"]
    assert not mcp_pubsub.is_subscribed(sess_id, "private.md")


def test_read_doc_auto_subscribes_at_head(client):
    """``read_doc(subscribe=true)`` is the default; HEAD reads register
    the session for future updates without an explicit
    ``resources/subscribe`` call."""
    uid = seed_user(uid="u1", email="u1@x.com")
    wiki_git.commit_file("doc.md", "# x\n", "seed", author=None)

    headers, sess_id = _handshake(client, _mint(uid))
    payload, is_error = _tool(client, headers, "read_doc", {"path": "doc.md"})
    assert not is_error, payload

    assert mcp_pubsub.is_subscribed(sess_id, "doc.md")


def test_read_doc_auto_subscribe_skipped_for_historical_sha(client):
    """Subscribing to a sha would be meaningless. Historical reads
    must NOT register the session."""
    uid = seed_user(uid="u1", email="u1@x.com")
    first = wiki_git.commit_file("doc.md", "# v1\n", "v1", author=None)
    wiki_git.commit_file("doc.md", "# v2\n", "v2", author=None)

    headers, sess_id = _handshake(client, _mint(uid))
    payload, _ = _tool(client, headers, "read_doc", {"path": "doc.md", "sha": first})
    assert payload["is_head"] is False
    assert not mcp_pubsub.is_subscribed(sess_id, "doc.md")


def test_read_doc_subscribe_false_disables_auto_subscribe(client):
    uid = seed_user(uid="u1", email="u1@x.com")
    wiki_git.commit_file("doc.md", "# x\n", "seed", author=None)

    headers, sess_id = _handshake(client, _mint(uid))
    _, _ = _tool(client, headers, "read_doc", {"path": "doc.md", "subscribe": False})
    assert not mcp_pubsub.is_subscribed(sess_id, "doc.md")


def test_unsubscribe_stops_delivery(client):
    uid = seed_user(uid="u1", email="u1@x.com")
    wiki_git.commit_file("page.md", "# x\n", "seed", author=None)

    headers, sess_id = _handshake(client, _mint(uid))
    _rpc(client, headers, "resources/subscribe", {"uri": "wiki:///page.md"})
    _rpc(client, headers, "resources/unsubscribe", {"uri": "wiki:///page.md"})

    mcp_pubsub.publish_doc_update("page.md", "x", "edit")
    # Queue is empty — drain timeout returns None.
    assert mcp_pubsub.drain_blocking(sess_id, timeout=0.1) is None


# --------------------------------------------------------------------------- #
# ACL recheck on publish                                                      #
# --------------------------------------------------------------------------- #


def test_publish_drops_subscriber_who_lost_acl(client):
    owner = seed_user(uid="owner", email="owner@x.com")
    reader = seed_user(uid="reader", email="reader@x.com")

    wiki_git.commit_file("doc.md", "# x\n", "seed", author=None)
    # Make doc readable by reader only via an explicit grant. We need
    # the doc to NOT have a public everyone-read row, so first set the
    # owner (which doesn't seed defaults), then grant reader read.
    wiki_acl.set_owner("doc.md", owner)
    grant_id = wiki_acl.grant(
        resource_kind="page",
        resource_path="doc.md",
        principal_kind="user",
        principal_id=reader,
        permission="read",
        granted_by_user_id=owner,
    )

    headers, sess_id = _handshake(client, _mint(reader))
    _rpc(client, headers, "resources/subscribe", {"uri": "wiki:///doc.md"})

    # Owner revokes reader's grant.
    wiki_acl.revoke(grant_id)

    # A subsequent publish should NOT be delivered — the per-subscriber
    # ACL recheck drops the notification AND the subscription.
    mcp_pubsub.publish_doc_update("doc.md", "abc", "edit")
    assert mcp_pubsub.drain_blocking(sess_id, timeout=0.1) is None
    assert not mcp_pubsub.is_subscribed(sess_id, "doc.md")


# --------------------------------------------------------------------------- #
# Hook into wiki/notify — end-to-end commit → push                            #
# --------------------------------------------------------------------------- #


def test_after_doc_write_hook_fans_to_subscribers(client):
    """End-to-end: subscribe via MCP, commit through the chat-agent
    write tool, the subscriber gets the notification."""
    uid = seed_user(uid="u1", email="u1@x.com")
    wiki_git.commit_file("page.md", "# x\nbefore\n", "seed", author=None)

    headers, sess_id = _handshake(client, _mint(uid))
    # Auto-subscribe via read_doc.
    payload, _ = _tool(client, headers, "read_doc", {"path": "page.md"})
    head = payload["sha"]
    assert mcp_pubsub.is_subscribed(sess_id, "page.md")

    # Commit through the MCP edit_doc — this exercises the same
    # commit_and_fan_out → wiki.notify.after_doc_write → publish_doc_update path.
    _, is_error = _tool(
        client,
        headers,
        "edit_doc",
        {
            "path": "page.md",
            "old_string": "before",
            "new_string": "after",
            "commit_message": "edit",
            "base_sha": head,
        },
    )
    assert not is_error

    notif = mcp_pubsub.drain_blocking(sess_id, timeout=2.0)
    assert notif is not None
    assert notif.method == "notifications/resources/updated"
    assert notif.params["uri"] == "wiki:///page.md"
    assert notif.params["changeKind"] == "edit"


def test_create_fires_list_changed_too(client):
    """A create commits the file AND changes the tree shape — both
    ``resources/updated`` and ``resources/list_changed`` should land
    in any subscribed session's queue."""
    uid = seed_user(uid="u1", email="u1@x.com")
    headers, sess_id = _handshake(client, _mint(uid))

    # Subscribe to a doc that doesn't yet exist — won't get the update,
    # but list_changed should still arrive because the queue exists for
    # the session (pubsub creates it on first subscribe / first push).
    _, _ = _tool(
        client,
        headers,
        "write_doc",
        {"path": "fresh.md", "body": "# fresh\n", "commit_message": "create"},
    )

    # Drain everything sitting in the queue.
    seen_methods: list[str] = []
    while True:
        n = mcp_pubsub.drain_blocking(sess_id, timeout=0.1)
        if n is None:
            break
        seen_methods.append(n.method)

    assert "notifications/resources/list_changed" in seen_methods


# --------------------------------------------------------------------------- #
# stale_paths                                                                 #
# --------------------------------------------------------------------------- #


def test_stale_paths_lists_paths_with_pending_pushes(client):
    """``stale_paths`` becomes meaningful in Phase 5: any subscribed
    path that has a pending push since the last tool call shows up
    here, non-destructively (the SSE writer still gets the
    notification)."""
    uid = seed_user(uid="u1", email="u1@x.com")
    wiki_git.commit_file("a.md", "# a\nfirst\n", "seed", author=None)
    wiki_git.commit_file("b.md", "# b\n", "seed", author=None)

    headers, sess_id = _handshake(client, _mint(uid))

    # Subscribe explicitly to both paths — read_doc would auto-subscribe
    # but using the explicit method here keeps the test minimal.
    _rpc(client, headers, "resources/subscribe", {"uri": "wiki:///a.md"})
    _rpc(client, headers, "resources/subscribe", {"uri": "wiki:///b.md"})

    # Simulate a remote commit on `a.md`.
    mcp_pubsub.publish_doc_update("a.md", "abc", "edit")

    # Any tool call returns stale_paths surfacing the pending update.
    payload, _ = _tool(client, headers, "search_wiki", {"query": "a"})
    assert "a.md" in payload["stale_paths"]
    assert "b.md" not in payload["stale_paths"]

    # The push is still in the queue for the SSE writer to ship.
    notif = mcp_pubsub.drain_blocking(sess_id, timeout=0.1)
    assert notif is not None
    assert notif.params["uri"] == "wiki:///a.md"


# --------------------------------------------------------------------------- #
# SSE stream                                                                  #
# --------------------------------------------------------------------------- #


def test_sse_get_requires_initialized_session(client):
    uid = seed_user(uid="u1", email="u1@x.com")
    raw = _mint(uid)
    auth = {"Authorization": f"Bearer {raw}"}

    res = client.get("/api/mcp", headers=auth)
    assert res.status_code == 400  # missing session id

    # Initialize but don't ack — session.initialized is still False.
    res = client.post(
        "/api/mcp",
        json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        headers=auth,
    )
    sess_id = res.headers["Mcp-Session-Id"]

    res = client.get("/api/mcp", headers={**auth, "Mcp-Session-Id": sess_id})
    assert res.status_code == 400


def test_sse_stream_delivers_notification_frame(client):
    """Open the SSE stream, publish to the subscribed session, read
    one frame off the stream, then close. The streaming response keeps
    sending heartbeats — we abort the iterator after we've seen what
    we wanted."""
    uid = seed_user(uid="u1", email="u1@x.com")
    wiki_git.commit_file("page.md", "# x\n", "seed", author=None)

    headers, sess_id = _handshake(client, _mint(uid))
    mcp_pubsub.subscribe_doc(sess_id, "page.md")
    # Pre-queue the notification so the stream sees it on the first iteration.
    mcp_pubsub.publish_doc_update("page.md", "shadetail", "edit")

    res = client.get("/api/mcp", headers=headers, buffered=False)
    assert res.status_code == 200
    assert "text/event-stream" in res.content_type

    # Read frames until we land on a real ``data:`` line.
    iterator = res.iter_encoded()
    found: dict[str, Any] | None = None
    for chunk in iterator:
        text = chunk.decode("utf-8", errors="ignore")
        for frame in text.split("\n\n"):
            data_lines = [line[5:].lstrip() for line in frame.splitlines() if line.startswith("data:")]
            if data_lines:
                found = json.loads("\n".join(data_lines))
                break
        if found is not None:
            break
    res.close()

    assert found is not None
    assert found["method"] == "notifications/resources/updated"
    assert found["params"]["uri"] == "wiki:///page.md"


def test_sse_stream_disconnect_cleans_up_session(client):
    uid = seed_user(uid="u1", email="u1@x.com")
    wiki_git.commit_file("page.md", "# x\n", "seed", author=None)

    headers, sess_id = _handshake(client, _mint(uid))
    mcp_pubsub.subscribe_doc(sess_id, "page.md")

    res = client.get("/api/mcp", headers=headers, buffered=False)
    res.close()
    # Closing the response triggers GeneratorExit → mcp_session.drop(sess_id),
    # which forgets the session and its subscriptions.
    assert mcp_session.get(sess_id) is None
    assert not mcp_pubsub.is_subscribed(sess_id, "page.md")


def test_sse_stream_rejects_session_owned_by_different_user(client):
    """Hijack attempt: alice mints a session, bob's bearer with
    alice's session id must be refused."""
    alice = seed_user(uid="alice", email="alice@x.com")
    bob = seed_user(uid="bob", email="bob@x.com")

    _, sess_id = _handshake(client, _mint(alice))
    bob_token = _mint(bob)

    res = client.get(
        "/api/mcp",
        headers={"Authorization": f"Bearer {bob_token}", "Mcp-Session-Id": sess_id},
    )
    assert res.status_code == 403
