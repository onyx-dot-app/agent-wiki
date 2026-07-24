"""Co-edit WebSocket surface (app/api/coedit.py) — the connect handshake,
per-message write-permission re-checks, and op/cursor/checkpoint/get_ops
message handling. Live delivery fan-out is covered at the channel level in
test_coedit_channel.py; here we exercise the WS route itself.

Pure transport swap from the old SSE-down + HTTP-POST-up protocol (see the
module docstring in app/api/coedit.py) — same domain logic, same message
shapes, one WebSocket instead of eight endpoints.
"""
from __future__ import annotations

import time
from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient
from starlette.testclient import WebSocketDenialResponse

from app.auth import users as users_repo
from app.main import create_app
from app.tasks.queues import coedit_queue
from app.wiki import acl, coedit, git

from tests._auth import login_fastapi

_PATH = "guides/setup.md"


@pytest.fixture
def client(tmp_db, tmp_repo):
    return TestClient(create_app())


def _seed_page(body: str = "# Setup\n\nhello\n") -> str:
    return git.commit_file(_PATH, body, message="seed", author="t <t@x.com>")


@contextmanager
def _ws(client, path: str = _PATH):
    """Open the coedit WS and yield `(ws, joined_frame)` once the handshake
    completes. Closing the `with` block closes the connection — the WS
    equivalent of the old `POST /leave` (see app/api/coedit.py: the server's
    disconnect handler is the leave signal, not a client message)."""
    with client.websocket_connect(f"/api/coedit/ws?path={path}") as ws:
        joined = ws.receive_json()
        assert joined["type"] == "joined"
        yield ws, joined


def _receive_typed(ws, expected_type: str) -> dict:
    """Skip past any broadcast frames (the sender's own op/cursor echoes,
    presence, etc.) to find the correlated reply — a connection sees its own
    broadcasts too, and there's no ordering guarantee worth hard-coding a
    test against beyond "the reply eventually arrives" (the server enqueues
    a reply before triggering its own broadcast, but a test asserting on
    exact interleaving would be pinned to that implementation detail, not
    the actual contract)."""
    for _ in range(10):
        frame = ws.receive_json()
        if frame["type"] == expected_type:
            return frame
    raise AssertionError(f"never received a {expected_type!r} frame")


def _op(ws, base_version: int, changes: list[dict], **extra) -> dict:
    ws.send_json({"type": "op", "request_id": "r", "base_version": base_version, "changes": changes, **extra})
    return _receive_typed(ws, "op_result")


def _apply_op(ws, base_version: int, changes: list[dict]) -> int:
    result = _op(ws, base_version, changes)
    assert result == {"type": "op_result", "request_id": "r", "ok": True, "version": result["version"], "error": None}
    return result["version"]


def _cursor(ws, **fields) -> None:
    ws.send_json({"type": "cursor", **fields})


def _checkpoint(ws) -> dict:
    ws.send_json({"type": "checkpoint", "request_id": "c"})
    return _receive_typed(ws, "checkpoint_result")


def _get_ops(ws, since_version: int) -> dict:
    ws.send_json({"type": "get_ops", "request_id": "g", "since_version": since_version})
    return _receive_typed(ws, "ops_result")


def _wait_for(predicate, timeout: float = 15.0) -> None:
    """Wait for a disconnect side effect. The server's disconnect handler is
    the leave signal, and nothing guarantees it has finished by the time
    ``websocket_connect.__exit__`` returns — the handler runs on the app's
    event loop/threads on its own schedule. Tests observe its *effects*, so
    they must wait for them, not assume an ordering the API doesn't offer."""
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        time.sleep(0.02)


def test_connect_requires_auth(client):
    with pytest.raises(WebSocketDenialResponse) as exc_info:
        with client.websocket_connect(f"/api/coedit/ws?path={_PATH}"):
            pass
    assert exc_info.value.status_code == 401


def test_connect_creates_session_seeded_from_head(client):
    uid = users_repo.create(email="ada@x.com", password="hunter2-x", name="Ada")
    login_fastapi(client, uid)
    sha = _seed_page()

    with _ws(client) as (_ws_conn, joined):
        assert joined["version"] == 0
        assert joined["buffer"] == "# Setup\n\nhello\n"
        assert joined["base_sha"] == sha
        assert [p["user_id"] for p in joined["participants"]] == [uid]


def test_connect_is_idempotent_and_shared(client):
    a = users_repo.create(email="ada@x.com", password="hunter2-x", name="Ada")
    b = users_repo.create(email="bo@x.com", password="hunter2-x", name="Bo")
    _seed_page()

    login_fastapi(client, a)
    with _ws(client) as (_ws_a, first):
        login_fastapi(client, b)
        with _ws(client) as (_ws_b, second):
            # Same shared session; both users are participants.
            assert first["session_id"] == second["session_id"]
            assert {p["user_id"] for p in second["participants"]} == {a, b}


def test_connect_without_read_is_forbidden(client):
    owner = users_repo.create(email="owner@x.com", password="hunter2-x", name="Owner")
    other = users_repo.create(email="other@x.com", password="hunter2-x", name="Other")
    _seed_page()
    # Committing via git directly bypasses the lifecycle hook, so the page has
    # no ACL rows — setting an owner makes it owner-only (no public grant).
    acl.set_owner(_PATH, owner)

    login_fastapi(client, other)
    with pytest.raises(WebSocketDenialResponse) as exc_info:
        with client.websocket_connect(f"/api/coedit/ws?path={_PATH}"):
            pass
    assert exc_info.value.status_code == 403


def test_read_only_user_joins_as_viewer_but_cannot_edit(client):
    # Connecting is page-open presence (read-gated); the write boundary is
    # per-message (op/cursor/checkpoint). A read-only user lands in the
    # roster; they never broadcast a caret, so presence renders them
    # "viewing" client-side.
    owner = users_repo.create(email="owner@x.com", password="hunter2-x", name="Owner")
    reader = users_repo.create(email="reader@x.com", password="hunter2-x", name="Reader")
    _seed_page()
    acl.set_owner(_PATH, owner)  # owner-only page...
    acl.grant(
        resource_kind="page",
        resource_path=_PATH,
        principal_kind="user",
        principal_id=reader,
        permission="read",
        granted_by_user_id=owner,
    )  # ...plus an explicit read grant for the viewer

    login_fastapi(client, reader)
    with _ws(client) as (ws, joined):
        me = [p for p in joined["participants"] if p["user_id"] == reader]
        assert me and me[0]["last_edited_at"] is None

        op_result = _op(ws, 0, [{"from": 0, "to": 0, "insert": "x"}])
        assert op_result == {"type": "op_result", "request_id": "r", "ok": False, "version": None, "error": "forbidden"}

        _cursor(ws, anchor=0, head=0, typing=False, seq=None)  # fire-and-forget, no reply to assert

    # The read tells the frontend not to offer editing at all.
    doc = client.get(f"/api/wiki/file?path={_PATH}")
    assert doc.status_code == 200
    assert doc.json()["can_write"] is False

    login_fastapi(client, owner)
    assert client.get(f"/api/wiki/file?path={_PATH}").json()["can_write"] is True


def test_op_stamps_last_edited_at(client):
    uid = users_repo.create(email="ada@x.com", password="hunter2-x", name="Ada")
    login_fastapi(client, uid)
    _seed_page()

    with _ws(client) as (ws, joined):
        assert joined["participants"][0]["last_edited_at"] is None
        _apply_op(ws, 0, [{"from": 0, "to": 0, "insert": "x"}])
        after = coedit.list_participants(joined["session_id"])
        me = [p for p in after if p.user_id == uid]
        assert me and me[0].last_edited_at is not None


def test_disconnect_removes_participant(client):
    uid = users_repo.create(email="ada@x.com", password="hunter2-x", name="Ada")
    login_fastapi(client, uid)
    _seed_page()

    with _ws(client) as (_ws_conn, joined):
        sid = joined["session_id"]
        assert len(coedit.list_participants(sid)) == 1

    _wait_for(lambda: coedit.list_participants(sid) == [])
    assert coedit.list_participants(sid) == []


def test_disconnect_of_last_participant_checkpoints(client):
    uid = users_repo.create(email="ada@x.com", password="hunter2-x", name="Ada")
    login_fastapi(client, uid)
    _seed_page("hello world")

    with coedit_queue.immediate_mode():
        with _ws(client) as (ws, joined):
            sid = joined["session_id"]
            _apply_op(ws, 0, [{"from": 0, "to": 5, "insert": "hi"}])
        # The disconnect handler enqueues the checkpoint; immediate_mode runs
        # it inline wherever the handler executes. Wait *inside* the block:
        # once the flag drops, a late enqueue would go to the real queue.
        _wait_for(
            lambda: git.read_file(_PATH) == "hi world"
            and coedit.get_active_session(_PATH) is None
        )

    session_after = coedit.get_active_session(_PATH)
    assert git.read_file(_PATH) == "hi world", (
        "checkpoint never landed: "
        f"session={'still open' if session_after else 'closed'}, "
        f"participants={coedit.list_participants(sid)}"
    )
    assert session_after is None
    assert sid is not None


def test_checkpoint_message_commits_buffer(client):
    uid = users_repo.create(email="ada@x.com", password="hunter2-x", name="Ada")
    login_fastapi(client, uid)
    _seed_page("hello world")

    with coedit_queue.immediate_mode():
        with _ws(client) as (ws, joined):
            sid = joined["session_id"]
            _apply_op(ws, 0, [{"from": 0, "to": 5, "insert": "hi"}])
            result = _checkpoint(ws)
            assert result == {"type": "checkpoint_result", "request_id": "c", "ok": True}
            # An explicit checkpoint doesn't close a session with an active
            # participant — must assert this before the connection closes;
            # disconnecting drops the last participant too, which (with an
            # already-clean buffer) triggers its own close.
            assert coedit.get_active_session(_PATH) is not None

    assert git.read_file(_PATH) == "hi world"
    assert sid is not None


def test_checkpoint_requires_write(client):
    # `other` needs *read* to connect at all (the WS handshake is read-gated
    # — see app/api/coedit.py) — granting only read, not write, is what
    # actually exercises the write-specific rejection; owner-only with no
    # grant for `other` would reject the connection itself, never reaching
    # the per-message check this test is about.
    owner = users_repo.create(email="owner@x.com", password="hunter2-x", name="Owner")
    other = users_repo.create(email="other@x.com", password="hunter2-x", name="Other")
    _seed_page()
    acl.set_owner(_PATH, owner)
    acl.grant(
        resource_kind="page",
        resource_path=_PATH,
        principal_kind="user",
        principal_id=other,
        permission="read",
        granted_by_user_id=owner,
    )
    login_fastapi(client, owner)
    with _ws(client) as (_owner_ws, _joined):
        login_fastapi(client, other)
        with _ws(client) as (ws, _joined2):
            result = _checkpoint(ws)
            assert result == {"type": "checkpoint_result", "request_id": "c", "ok": False}


def _login_and_join(client, email="ada@x.com"):
    uid = users_repo.create(email=email, password="hunter2-x", name="Ada")
    login_fastapi(client, uid)
    _seed_page("hello world")
    return client.websocket_connect(f"/api/coedit/ws?path={_PATH}")


def test_op_applies_and_returns_version(client):
    with _login_and_join(client) as ws:
        joined = ws.receive_json()  # buffer seeded as "hello world"
        version = _apply_op(ws, 0, [{"from": 0, "to": 5, "insert": "hi"}])
        assert version == 1
        # get_ops reflects the applied edit.
        ops = _get_ops(ws, 0)
        assert ops["current_head_version"] == 1
        assert ops["ops"][0]["changes"] == [{"from": 0, "to": 5, "insert": "hi"}]
        assert joined["session_id"]  # sanity: joined before the op


def test_op_stale_base_version_is_stale_version_error(client):
    with _login_and_join(client) as ws:
        ws.receive_json()
        _apply_op(ws, 0, [{"from": 0, "to": 0, "insert": "x"}])
        result = _op(ws, 0, [{"from": 0, "to": 0, "insert": "y"}])
        assert result == {"type": "op_result", "request_id": "r", "ok": False, "version": None, "error": "stale_version"}


def test_op_out_of_bounds_is_invalid_op_error(client):
    with _login_and_join(client) as ws:
        ws.receive_json()
        result = _op(ws, 0, [{"from": 0, "to": 9999, "insert": "x"}])
        assert result == {"type": "op_result", "request_id": "r", "ok": False, "version": None, "error": "invalid_op"}


def test_op_malformed_change_logs_and_drops_silently(client):
    # Missing 'to' fails pydantic validation inside the recv loop — logged
    # and dropped (see app/api/coedit.py's ValidationError handling), not a
    # per-message error reply, since a malformed frame has no valid
    # request_id to correlate a reply against. A well-formed op sent right
    # after still gets a normal reply, proving the connection survives it.
    with _login_and_join(client) as ws:
        ws.receive_json()
        ws.send_json({"type": "op", "request_id": "bad", "base_version": 0, "changes": [{"from": 0, "insert": "x"}]})
        version = _apply_op(ws, 0, [{"from": 0, "to": 0, "insert": "ok"}])
        assert version == 1


def test_cursor_broadcasts_to_peers(client):
    a = users_repo.create(email="ada@x.com", password="hunter2-x", name="Ada")
    b = users_repo.create(email="bo@x.com", password="hunter2-x", name="Bo")
    _seed_page("hello world")

    login_fastapi(client, a)
    with _ws(client) as (ws_a, _joined_a):
        login_fastapi(client, b)
        with _ws(client) as (ws_b, _joined_b):
            ws_a.receive_json()  # the presence frame announcing b's join
            _cursor(ws_a, anchor=0, head=5, typing=True, seq=1)
            frame = ws_b.receive_json()
            assert frame["type"] == "cursor"
            assert frame["user_id"] == a
            assert frame["anchor"] == 0 and frame["head"] == 5


def test_cursor_clear_broadcasts_null_anchor(client):
    # A null-position cursor (editor blur / tab hidden) is a caret clear —
    # broadcast to the session so peers drop the caret; nothing persisted.
    a = users_repo.create(email="ada@x.com", password="hunter2-x", name="Ada")
    b = users_repo.create(email="bo@x.com", password="hunter2-x", name="Bo")
    _seed_page("hello world")

    login_fastapi(client, a)
    with _ws(client) as (ws_a, _joined_a):
        login_fastapi(client, b)
        with _ws(client) as (ws_b, _joined_b):
            ws_a.receive_json()  # presence
            _cursor(ws_a, anchor=None, head=None, typing=False, seq=2)
            frame = ws_b.receive_json()
            assert frame["type"] == "cursor"
            assert frame["anchor"] is None and frame["head"] is None


def test_cursor_requires_write(client):
    # See test_checkpoint_requires_write's comment: `other` needs read to
    # connect at all — granting only read (not write) is what exercises the
    # write-specific rejection this test is actually about.
    owner = users_repo.create(email="owner@x.com", password="hunter2-x", name="Owner")
    other = users_repo.create(email="other@x.com", password="hunter2-x", name="Other")
    _seed_page()
    acl.set_owner(_PATH, owner)
    acl.grant(
        resource_kind="page",
        resource_path=_PATH,
        principal_kind="user",
        principal_id=other,
        permission="read",
        granted_by_user_id=owner,
    )
    login_fastapi(client, owner)
    with _ws(client) as (owner_ws, _joined):
        login_fastapi(client, other)
        with _ws(client) as (ws, _joined2):
            owner_ws.receive_json()  # presence frame for other's join
            _cursor(ws, anchor=0, head=0, typing=False, seq=None)
            # Cursor is fire-and-forget (no result frame to assert on
            # directly — see app/api/coedit.py), so prove the rejection by
            # racing it against a legitimate, always-broadcast op from the
            # owner: broadcasts (including a sender's own) land on every
            # connection in arrival order, so if `other`'s connection sees
            # the owner's op *before* any cursor frame, their own cursor was
            # dropped rather than merely reordered behind it.
            result = _op(owner_ws, 0, [{"from": 0, "to": 0, "insert": "x"}])
            assert result["ok"] is True
            first = ws.receive_json()
            assert first["type"] == "op"


def test_op_requires_write(client):
    # See test_checkpoint_requires_write's comment: `other` needs read to
    # connect at all — granting only read (not write) is what exercises the
    # write-specific rejection this test is actually about.
    owner = users_repo.create(email="owner@x.com", password="hunter2-x", name="Owner")
    other = users_repo.create(email="other@x.com", password="hunter2-x", name="Other")
    _seed_page()
    acl.set_owner(_PATH, owner)
    acl.grant(
        resource_kind="page",
        resource_path=_PATH,
        principal_kind="user",
        principal_id=other,
        permission="read",
        granted_by_user_id=owner,
    )
    login_fastapi(client, owner)
    with _ws(client) as (_owner_ws, _joined):
        login_fastapi(client, other)
        with _ws(client) as (ws, _joined2):
            result = _op(ws, 0, [{"from": 0, "to": 0, "insert": "x"}])
            assert result == {"type": "op_result", "request_id": "r", "ok": False, "version": None, "error": "forbidden"}


def test_write_permission_revoked_mid_session_is_enforced_on_next_message(client):
    # The one behavior this migration was explicit about preserving: unlike
    # the other (Yjs) WS work's connect-time-only gate, write permission is
    # re-checked on every message, not just at connect — a mid-session ACL
    # change takes effect immediately, not just on the next reconnect.
    owner = users_repo.create(email="owner@x.com", password="hunter2-x", name="Owner")
    editor = users_repo.create(email="editor@x.com", password="hunter2-x", name="Editor")
    _seed_page("hello world")
    acl.set_owner(_PATH, owner)
    entry_id = acl.grant(
        resource_kind="page",
        resource_path=_PATH,
        principal_kind="user",
        principal_id=editor,
        permission="write",
        granted_by_user_id=owner,
    )

    login_fastapi(client, editor)
    with _ws(client) as (ws, _joined):
        assert _apply_op(ws, 0, [{"from": 0, "to": 0, "insert": "x"}]) == 1
        acl.revoke(entry_id)
        result = _op(ws, 1, [{"from": 0, "to": 0, "insert": "y"}])
        assert result == {"type": "op_result", "request_id": "r", "ok": False, "version": None, "error": "forbidden"}


def test_get_ops_returns_missed_changes_for_rebase(client):
    with _login_and_join(client) as ws:
        ws.receive_json()
        v1 = _apply_op(ws, 0, [{"from": 0, "to": 0, "insert": "X"}])
        v2 = _apply_op(ws, v1, [{"from": 0, "to": 0, "insert": "Y"}])

        # since_version=0 → both ops, oldest first, wire-shaped like op frames ("from" alias).
        body = _get_ops(ws, 0)
        assert body["current_head_version"] == v2
        assert [o["version"] for o in body["ops"]] == [v1, v2]
        assert body["ops"][0]["changes"] == [{"from": 0, "to": 0, "insert": "X"}]

        # since_version=v1 → only the op after it.
        body2 = _get_ops(ws, v1)
        assert [o["version"] for o in body2["ops"]] == [v2]

        # since_version=head → nothing missed.
        assert _get_ops(ws, v2)["ops"] == []


def test_op_client_id_round_trips_to_get_ops(client):
    with _login_and_join(client) as ws:
        ws.receive_json()
        # Op tagged with a per-connection client id.
        _op(ws, 0, [{"from": 0, "to": 0, "insert": "X"}], client_id="cli_abc")
        op = _get_ops(ws, 0)["ops"][0]
        assert op["client_id"] == "cli_abc"

        # Omitting client_id (non-collab client) is fine — it's null.
        _op(ws, 1, [{"from": 0, "to": 0, "insert": "Y"}])
        ops = _get_ops(ws, 1)["ops"]
        assert ops[0]["client_id"] is None


def test_file_read_serves_live_buffer_during_session(client):
    # GET /wiki/file is session-aware: while a session is open, it serves the
    # live Postgres buffer, so an edit is visible immediately — no dependency on
    # the async checkpoint commit (git HEAD is unchanged here).
    uid = users_repo.create(email="ada@x.com", password="hunter2-x", name="Ada")
    login_fastapi(client, uid)
    sha = _seed_page("# Setup\n\nhello\n")
    with _ws(client) as (ws, _joined):
        _apply_op(ws, 0, [{"from": 0, "to": len("# Setup\n\nhello\n"), "insert": "LIVE\n"}])

        resp = client.get(f"/api/wiki/file?path={_PATH}")
        assert resp.status_code == 200
        body = resp.json()
        assert body["body"] == "LIVE\n"  # buffer, not committed HEAD
        assert body["head_sha"] == sha  # HEAD still the pre-session commit
        # git working tree is untouched — nothing was committed.
        assert git.read_file(_PATH) == "# Setup\n\nhello\n"


def test_file_read_merges_agent_commit_over_live_buffer(client):
    # Safety net: when an agent commits to git after the session opened (HEAD
    # moves past base_sha), the read quick-merges the committed change over the
    # buffer so a viewer sees both the in-session edit and the agent's edit.
    uid = users_repo.create(email="ada@x.com", password="hunter2-x", name="Ada")
    login_fastapi(client, uid)
    doc = "one\ntwo\nthree\nfour\nfive\n"
    _seed_page(doc)
    with _ws(client) as (ws, _joined):
        # Human edits the first line in the buffer...
        _apply_op(ws, 0, [{"from": 0, "to": 3, "insert": "ONE"}])
        # ...an agent commits a distant, non-overlapping change out of band.
        git.commit_file(_PATH, "one\ntwo\nthree\nfour\nFIVE\n", message="agent", author="A <a@x.com>")

        body = client.get(f"/api/wiki/file?path={_PATH}").json()["body"]
        assert body == "ONE\ntwo\nthree\nfour\nFIVE\n"  # both edits, no LLM, no commit


def test_file_read_serves_head_when_buffer_conflicts_with_committed_change(client):
    # When an agent commits a change that OVERLAPS the in-session edit (HEAD
    # moves past base_sha, the 3-way merge conflicts), the read serves committed
    # HEAD rather than the buffer. Preferring the buffer here would let a stale
    # session (in the limit, a zombie with no one left to reconcile it) hide the
    # committed change from every viewer — the 2026-07-06 incident.
    uid = users_repo.create(email="ada@x.com", password="hunter2-x", name="Ada")
    login_fastapi(client, uid)
    _seed_page("one\ntwo\nthree\n")
    with _ws(client) as (ws, _joined):
        # Human edits the first line in the buffer...
        _apply_op(ws, 0, [{"from": 0, "to": 3, "insert": "BUFFER"}])
        # ...an agent commits a CONFLICTING change to the same first line out of band.
        git.commit_file(_PATH, "AGENT\ntwo\nthree\n", message="agent", author="A <a@x.com>")

        body = client.get(f"/api/wiki/file?path={_PATH}").json()["body"]
        assert body == "AGENT\ntwo\nthree\n"  # committed HEAD, not the stale buffer


def test_file_read_serves_buffer_at_zero_participants_when_no_conflict(client):
    # A departed editor's un-committed edit stays visible (no ~checkpoint-delay
    # gap) as long as it doesn't conflict with a moved HEAD: base_sha == HEAD, so
    # the buffer already reflects everything and the read serves it verbatim even
    # with no participants left. Safety is keyed on conflict-with-HEAD, not on
    # whether anyone is still in the session.
    uid = users_repo.create(email="ada@x.com", password="hunter2-x", name="Ada")
    login_fastapi(client, uid)
    _seed_page("# Setup\n\nhello\n")
    with _ws(client) as (ws, joined):
        _apply_op(ws, 0, [{"from": 0, "to": len("# Setup\n\nhello\n"), "insert": "LIVE\n"}])
        coedit.leave(joined["session_id"], uid)  # everyone leaves; session stays active (zombie)
        st = coedit.get_active_session(_PATH)
        assert st is not None and st.status == "active"

        body = client.get(f"/api/wiki/file?path={_PATH}").json()["body"]
        assert body == "LIVE\n"  # HEAD hasn't moved → buffer still safe to serve
