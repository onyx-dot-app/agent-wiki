"""Phase 6 — async ``update_doc_nl`` job lifecycle, idempotency,
debounce, base_sha recheck, ACL enforcement, ``job://<id>`` resource
read + subscribe, and worker-side ``g.user`` reconstitution.

We run the worker task synchronously via
``documents_queue.immediate_mode()`` so we can assert on the post-run
state directly. Cross-process LISTEN/NOTIFY is not exercised — same-
process delivery is the path we test.

The LLM call is patched at the seam (``app.llm.client.complete``) per
the testing rules in CLAUDE.md.
"""
from __future__ import annotations

import json
from contextlib import ExitStack
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.auth import mcp_tokens as tokens_repo
from app.main import create_app
from app.mcp_server import jobs as mcp_jobs_repo
from app.mcp_server import pubsub as mcp_pubsub
from app.mcp_server import session as mcp_session
from app.tasks.queues import documents_queue
from app.wiki import acl as wiki_acl
from app.wiki import git as wiki_git

from tests._seed import seed_user


@pytest.fixture
def client(tmp_repo):
    mcp_session.reset_for_tests()
    yield TestClient(create_app())
    mcp_session.reset_for_tests()


@pytest.fixture
def llm_returns(monkeypatch):
    """Helper: ``llm_returns("new body")`` patches the LLM seam to
    return that body for every call. Use ``llm_returns(...)`` returning
    ``NO_CHANGE`` to exercise the no-change path.

    Patches ``app.llm.client.complete`` per CLAUDE.md test rules — we
    never mock the SDK directly.
    """
    from app.llm.client import CompletionResult, Usage

    def install(text: str) -> None:
        def fake(messages, **kwargs):
            return CompletionResult(text=text, tool_calls=[], stop_reason="end_turn", usage=Usage())
        monkeypatch.setattr("app.llm.client.complete", fake)
        # The caller in app.llm.agents.wiki_updater imports
        # ``client`` and calls ``client.complete(...)``; patching the
        # bare module attribute is enough.

    return install


@pytest.fixture
def immediate_documents():
    """Run ``documents_queue`` tasks synchronously inside the test so
    we can assert on the post-run job row without spinning up a worker."""
    with ExitStack() as stack:
        stack.enter_context(documents_queue.immediate_mode())
        yield


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


def _call_tool(
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


def _read_doc(client, headers, path: str) -> dict[str, Any]:
    payload, is_error = _call_tool(client, headers, "read_doc", {"path": path})
    assert not is_error, payload
    return payload


# --------------------------------------------------------------------------- #
# Happy path                                                                  #
# --------------------------------------------------------------------------- #


def test_update_doc_nl_enqueue_runs_to_committed(
    client, llm_returns, immediate_documents
):
    uid = seed_user(uid="u1", email="u1@x.com")
    wiki_git.commit_file("doc.md", "# Doc\n\noriginal text\n", "seed", author=None)

    headers, _ = _handshake(client, _mint(uid))

    llm_returns("# Doc\n\nrewritten by the agent\n")

    payload, is_error = _call_tool(
        client,
        headers,
        "update_doc_nl",
        {"path": "doc.md", "instruction": "rewrite this please"},
    )
    assert not is_error, payload

    job_id = payload["job_id"]
    assert payload["status_uri"] == f"job://{job_id}"
    # Immediate mode ran the worker synchronously — terminal state.
    job = mcp_jobs_repo.get(job_id)
    assert job is not None
    assert job["status"] == "succeeded"
    assert job["result"]["committed"] is True
    assert "sha" in job["result"]

    # The wiki was committed with the new body.
    body = wiki_git.read_file("doc.md")
    assert "rewritten by the agent" in body


def test_update_doc_nl_no_change_marks_succeeded_uncommitted(
    client, llm_returns, immediate_documents
):
    uid = seed_user(uid="u1", email="u1@x.com")
    wiki_git.commit_file("doc.md", "# Doc\n\nbody\n", "seed", author=None)

    headers, _ = _handshake(client, _mint(uid))

    llm_returns("NO_CHANGE")

    payload, is_error = _call_tool(
        client,
        headers,
        "update_doc_nl",
        {"path": "doc.md", "instruction": "do nothing"},
    )
    assert not is_error, payload

    job = mcp_jobs_repo.get(payload["job_id"])
    assert job is not None
    assert job["status"] == "succeeded"
    assert job["result"]["committed"] is False
    assert job["result"]["reason"] == "no_change"


# --------------------------------------------------------------------------- #
# Validation                                                                  #
# --------------------------------------------------------------------------- #


def test_update_doc_nl_rejects_missing_instruction(client):
    uid = seed_user(uid="u1", email="u1@x.com")
    wiki_git.commit_file("doc.md", "# x\n", "seed", author=None)

    headers, _ = _handshake(client, _mint(uid))
    payload, is_error = _call_tool(
        client, headers, "update_doc_nl", {"path": "doc.md"}
    )
    assert is_error
    assert "instruction" in payload["error"]


def test_update_doc_nl_rejects_blocked_acl(client):
    """User without write access can't enqueue."""
    owner = seed_user(uid="owner", email="owner@x.com")
    reader = seed_user(uid="reader", email="reader@x.com")

    wiki_git.commit_file("doc.md", "# x\nbody\n", "seed", author=None)
    wiki_acl.set_owner("doc.md", owner)
    wiki_acl.grant(
        resource_kind="page",
        resource_path="doc.md",
        principal_kind="user",
        principal_id=reader,
        permission="read",
        granted_by_user_id=owner,
    )

    headers, _ = _handshake(client, _mint(reader))

    payload, is_error = _call_tool(
        client,
        headers,
        "update_doc_nl",
        {"path": "doc.md", "instruction": "edit"},
    )
    assert is_error
    assert "forbidden" in payload["error"]


# --------------------------------------------------------------------------- #
# Idempotency                                                                 #
# --------------------------------------------------------------------------- #


def test_update_doc_nl_idempotent_with_explicit_key(
    client, llm_returns, immediate_documents
):
    uid = seed_user(uid="u1", email="u1@x.com")
    wiki_git.commit_file("doc.md", "# x\nbefore\n", "seed", author=None)

    headers, _ = _handshake(client, _mint(uid))

    llm_returns("# x\nafter\n")

    args = {
        "path": "doc.md",
        "instruction": "rewrite please",
        "idempotency_key": "explicit-deadbeef",
    }
    first, _ = _call_tool(client, headers, "update_doc_nl", args)
    second, _ = _call_tool(client, headers, "update_doc_nl", args)

    assert first["job_id"] == second["job_id"]
    assert first["deduplicated"] is False
    assert second["deduplicated"] is True


def test_update_doc_nl_default_idempotency_collapses_retries(
    client, llm_returns, immediate_documents
):
    """No explicit key — server defaults to sha256(user|path|instruction)
    so a retry of the same instruction collapses without the agent
    having to mint a key."""
    uid = seed_user(uid="u1", email="u1@x.com")
    wiki_git.commit_file("doc.md", "# x\n", "seed", author=None)

    headers, _ = _handshake(client, _mint(uid))

    llm_returns("NO_CHANGE")

    args = {"path": "doc.md", "instruction": "do nothing"}
    first, _ = _call_tool(client, headers, "update_doc_nl", args)
    second, _ = _call_tool(client, headers, "update_doc_nl", args)
    assert first["job_id"] == second["job_id"]


def test_different_instructions_get_different_jobs(
    client, llm_returns, immediate_documents
):
    uid = seed_user(uid="u1", email="u1@x.com")
    wiki_git.commit_file("doc.md", "# x\n", "seed", author=None)

    headers, _ = _handshake(client, _mint(uid))

    llm_returns("NO_CHANGE")

    a, _ = _call_tool(
        client, headers, "update_doc_nl", {"path": "doc.md", "instruction": "alpha"}
    )
    b, _ = _call_tool(
        client, headers, "update_doc_nl", {"path": "doc.md", "instruction": "beta"}
    )
    assert a["job_id"] != b["job_id"]


# --------------------------------------------------------------------------- #
# base_sha recheck inside the worker                                          #
# --------------------------------------------------------------------------- #


def test_update_doc_nl_merges_concurrent_change_in_worker(
    client, llm_returns, immediate_documents
):
    """A concurrent commit between enqueue and worker run no longer fails the
    job. The sub-agent's regenerated body is 3-way merged against the
    concurrent change, so the job succeeds and commits."""
    uid = seed_user(uid="u1", email="u1@x.com")
    wiki_git.commit_file("doc.md", "# Doc\n\nAlpha.\n\nBeta.\n", "v1", author=None)

    headers, _ = _handshake(client, _mint(uid))
    _read_doc(client, headers, "doc.md")

    # Someone else commits a non-overlapping change — HEAD advances. The worker
    # reads this current body and hands it to the sub-agent.
    wiki_git.commit_file("doc.md", "# Doc\n\nAlpha.\n\nBeta.\n\nGamma.\n", "v2", author=None)

    # Sub-agent revises Alpha against the current (post-Gamma) body.
    llm_returns("# Doc\n\nAlpha (revised).\n\nBeta.\n\nGamma.\n")

    payload, is_error = _call_tool(
        client,
        headers,
        "update_doc_nl",
        {"path": "doc.md", "instruction": "revise alpha"},
    )
    assert not is_error, payload

    job = mcp_jobs_repo.get(payload["job_id"])
    assert job is not None
    assert job["status"] == "succeeded", job
    assert job["result"]["committed"] is True

    merged = wiki_git.read_file("doc.md")
    assert "Alpha (revised)." in merged
    assert "Gamma." in merged  # the concurrent change was preserved


# --------------------------------------------------------------------------- #
# Debounce                                                                    #
# --------------------------------------------------------------------------- #


def test_update_doc_nl_debounce_skips_when_recent_succeeded(
    client, llm_returns, immediate_documents
):
    """Two distinct instructions back-to-back on the same (user, path).
    The first commits, the second is debounced — the worker skips the
    LLM call and marks the job succeeded with reason=debounced."""
    uid = seed_user(uid="u1", email="u1@x.com")
    wiki_git.commit_file("doc.md", "# x\nbefore\n", "seed", author=None)

    headers, _ = _handshake(client, _mint(uid))

    llm_returns("# x\nafter\n")

    first, _ = _call_tool(
        client,
        headers,
        "update_doc_nl",
        {"path": "doc.md", "instruction": "first edit"},
    )
    job_one = mcp_jobs_repo.get(first["job_id"])
    assert job_one is not None
    assert job_one["status"] == "succeeded"
    assert job_one["result"]["committed"] is True

    # Second instruction would otherwise commit too — but it's within
    # the debounce window so the worker skips the LLM call.
    second, _ = _call_tool(
        client,
        headers,
        "update_doc_nl",
        {"path": "doc.md", "instruction": "second edit"},
    )
    job_two = mcp_jobs_repo.get(second["job_id"])
    assert job_two is not None
    assert job_two["status"] == "succeeded"
    assert job_two["result"]["committed"] is False
    assert job_two["result"]["reason"] == "debounced"
    assert job_two["result"]["previous_job_id"] == first["job_id"]


# --------------------------------------------------------------------------- #
# job:// resource surface                                                     #
# --------------------------------------------------------------------------- #


def test_resources_read_returns_job_state(
    client, llm_returns, immediate_documents
):
    uid = seed_user(uid="u1", email="u1@x.com")
    wiki_git.commit_file("doc.md", "# x\n", "seed", author=None)

    headers, _ = _handshake(client, _mint(uid))
    llm_returns("# x\nedited\n")

    payload, _ = _call_tool(
        client,
        headers,
        "update_doc_nl",
        {"path": "doc.md", "instruction": "edit"},
    )
    job_id = payload["job_id"]

    res = client.post(
        "/api/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 7,
            "method": "resources/read",
            "params": {"uri": f"job://{job_id}"},
        },
        headers=headers,
    )
    body = res.json()
    contents = body["result"]["contents"]
    assert len(contents) == 1
    assert contents[0]["mimeType"] == "application/json"

    public = json.loads(contents[0]["text"])
    assert public["id"] == job_id
    assert public["status"] == "succeeded"
    # The user_id field must NOT be exposed publicly.
    assert "user_id" not in public


def test_resources_read_job_belonging_to_other_user_is_404(
    client, llm_returns, immediate_documents
):
    alice = seed_user(uid="alice", email="alice@x.com")
    bob = seed_user(uid="bob", email="bob@x.com")
    wiki_git.commit_file("doc.md", "# x\n", "seed", author=None)

    # Alice enqueues a job.
    alice_headers, _ = _handshake(client, _mint(alice))
    llm_returns("# x\nedit\n")
    alice_payload, _ = _call_tool(
        client,
        alice_headers,
        "update_doc_nl",
        {"path": "doc.md", "instruction": "edit"},
    )
    job_id = alice_payload["job_id"]

    # Bob tries to read it — must come back as not found, not as a
    # forbidden (don't leak that the id exists).
    bob_headers, _ = _handshake(client, _mint(bob))
    res = client.post(
        "/api/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 7,
            "method": "resources/read",
            "params": {"uri": f"job://{job_id}"},
        },
        headers=bob_headers,
    )
    body = res.json()
    assert body["error"]["code"] == -32602  # invalid_params (not found)


def test_subscribe_to_own_job_then_publish_lands(
    client, llm_returns, immediate_documents
):
    """The async wrapper auto-subscribes the calling session, but
    explicit ``resources/subscribe`` should also work for a session
    that learned the job_id out-of-band (e.g. from a sibling
    session's ``deduplicated=true`` response)."""
    uid = seed_user(uid="u1", email="u1@x.com")
    wiki_git.commit_file("doc.md", "# x\n", "seed", author=None)

    # Pre-create a job by another flow (we'll just call the repo
    # directly so the caller-session isn't auto-subscribed).
    job = mcp_jobs_repo.create(
        user_id=uid,
        kind=mcp_jobs_repo.KIND_UPDATE_DOC_NL,
        payload={"path": "doc.md", "instruction": "x", "base_sha": None},
        idempotency_key=None,
    )

    headers, sess_id = _handshake(client, _mint(uid))
    res = client.post(
        "/api/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 8,
            "method": "resources/subscribe",
            "params": {"uri": f"job://{job['id']}"},
        },
        headers=headers,
    )
    assert res.json()["result"] == {}
    assert mcp_pubsub.is_subscribed_job(sess_id, job["id"])

    # Publish — the queue receives the notification.
    mcp_pubsub.publish_job_update(job["id"], "succeeded")
    notif = mcp_pubsub.drain_blocking(sess_id, timeout=2.0)
    assert notif is not None
    assert notif.params["uri"] == f"job://{job['id']}"
    assert notif.params["status"] == "succeeded"


def test_subscribe_to_someone_elses_job_is_forbidden(client):
    alice = seed_user(uid="alice", email="alice@x.com")
    bob = seed_user(uid="bob", email="bob@x.com")

    job = mcp_jobs_repo.create(
        user_id=alice,
        kind=mcp_jobs_repo.KIND_UPDATE_DOC_NL,
        payload={"path": "doc.md", "instruction": "x", "base_sha": None},
        idempotency_key=None,
    )

    bob_headers, _ = _handshake(client, _mint(bob))
    res = client.post(
        "/api/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 9,
            "method": "resources/subscribe",
            "params": {"uri": f"job://{job['id']}"},
        },
        headers=bob_headers,
    )
    body = res.json()
    assert "error" in body
    assert "forbidden" in body["error"]["message"]


# --------------------------------------------------------------------------- #
# Worker → publish → SSE end-to-end (in-process)                              #
# --------------------------------------------------------------------------- #


def test_async_wrapper_auto_subscribes_session_to_job(
    client, llm_returns, immediate_documents
):
    """Per the design: the wrapper auto-subscribes so the calling
    session sees status pushes via SSE without a separate subscribe."""
    uid = seed_user(uid="u1", email="u1@x.com")
    wiki_git.commit_file("doc.md", "# x\n", "seed", author=None)

    headers, sess_id = _handshake(client, _mint(uid))
    llm_returns("# x\nedit\n")

    payload, _ = _call_tool(
        client,
        headers,
        "update_doc_nl",
        {"path": "doc.md", "instruction": "edit"},
    )
    job_id = payload["job_id"]

    assert mcp_pubsub.is_subscribed_job(sess_id, job_id)

    # The terminal status (succeeded) was published by the worker
    # while the wrapper was still on the stack — drain the queue and
    # confirm the frame is there.
    seen_statuses: list[str] = []
    while True:
        notif = mcp_pubsub.drain_blocking(sess_id, timeout=0.05)
        if notif is None:
            break
        if notif.params.get("uri") == f"job://{job_id}":
            seen_statuses.append(notif.params["status"])
    assert "succeeded" in seen_statuses
