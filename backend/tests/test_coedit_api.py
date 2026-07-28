"""Co-edit WebSocket surface (app/api/coedit.py) — the connect handshake,
per-message write-permission re-checks, and Yjs sync/awareness/checkpoint
message handling. Live delivery fan-out is covered at the channel level in
test_coedit_channel.py; here we exercise the WS route itself.

Rewritten for the Yjs binary protocol (app/api/coedit.py's own module
docstring) — document content and cursor/awareness both travel as raw
pycrdt sync/awareness bytes over WS binary frames; only `joined`/`presence`/
`checkpoint`/`checkpoint_result`/`resync` remain JSON text frames. The old
OT-era protocol this file used to test (`op`/`get_ops`/`cursor` JSON
messages, `base_version`/`version` fields, `stale_version`/`invalid_op`
error codes) no longer exists: CRDT merges never reject a "stale" update
(concurrent edits merge instead of one winning), so there's nothing left to
test for those two error codes, and catch-up-on-reconnect is now an
intrinsic property of the sync handshake itself (see
test_reconnect_receives_current_content_after_missed_edit) rather than a
separate `get_ops` message.
"""
from __future__ import annotations

import asyncio
import json
import time
from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient
from pycrdt import (
    Awareness,
    Doc,
    XmlFragment,
    YMessageType,
    create_awareness_message,
    create_sync_message,
    create_update_message,
    handle_sync_message,
    read_message,
)
from starlette.testclient import WebSocketDenialResponse

from app.auth import users as users_repo
from app.main import create_app
from app.tasks.queues import coedit_queue
from app.wiki import acl, coedit, coedit_room, git
from app.wiki.markdown_yjs import ROOT_XML_KEY, reconstruct_body

from tests._auth import login_fastapi

_PATH = "guides/setup.md"


@pytest.fixture
def client(tmp_db, tmp_repo):
    return TestClient(create_app())


def _seed_page(body: str = "# Setup\n\nhello\n") -> str:
    return git.commit_file(_PATH, body, message="seed", author="t <t@x.com>")


def _recv_bytes(ws, max_frames: int = 10) -> bytes:
    """Skip past any JSON control frames (presence, etc.) to find the next
    binary Yjs frame."""
    for _ in range(max_frames):
        msg = ws.receive()
        data = msg.get("bytes")
        if data is not None:
            return data
    raise AssertionError("never received a bytes frame")


def _recv_typed_json(ws, expected_type: str, max_frames: int = 10) -> dict:
    """Skip past any binary Yjs frames to find a JSON control frame of the
    given type — mirrors the old file's `_receive_typed`: a connection sees
    its own broadcasts too, and there's no ordering guarantee worth hard-
    coding a test against beyond "the frame eventually arrives"."""
    for _ in range(max_frames):
        msg = ws.receive()
        text = msg.get("text")
        if text is None:
            continue
        frame = json.loads(text)
        if frame.get("type") == expected_type:
            return frame
    raise AssertionError(f"never received a {expected_type!r} frame")


@contextmanager
def _ws(client, path: str = _PATH):
    """Open the coedit WS, complete the Yjs sync handshake, and yield
    ``(ws, joined, doc)`` — ``doc`` is a fresh client-side ``pycrdt.Doc``
    synced to the server's current content by the time this yields.

    Sequencing is safe without a skip-loop here: ``ws()`` sends `joined`
    then its own SYNC_STEP1 query synchronously, before the recv/send task
    loops (and so any broadcast this connection could see) ever start.

    Also (re)binds ``coedit_room``'s main loop to this connection's own
    portal: unlike the real app (whose lifespan binds it once, see
    app/main.py), ``TestClient`` spins up a fresh, independent event loop
    for *every* ``websocket_connect``/HTTP call (confirmed directly in
    starlette's own testclient.py — neither the WS portal nor a plain
    ``client.get`` call ever share one), so nothing ever binds it here on
    its own. Without this, any live-buffer read (``GET /wiki/file`` ->
    ``coedit_room.read_body_sync``) while this connection is open raises
    "main loop not bound yet". Rebinding to the still-open WS connection's
    own loop lets a subsequent ``client.get`` (on its own, different loop)
    schedule the read onto it via the normal cross-thread path.
    """
    with client.websocket_connect(f"/api/coedit/ws?path={path}") as ws:
        coedit_room.bind_main_loop(ws.portal.call(asyncio.get_running_loop))
        joined = ws.receive_json()
        assert joined["type"] == "joined"
        ws.receive_bytes()  # the server's own SYNC_STEP1 query — nothing to reply with
        doc = Doc()
        ws.send_bytes(create_sync_message(doc))  # our SYNC_STEP1 -> prompts its SYNC_STEP2
        reply = ws.receive_bytes()
        assert reply[0] == YMessageType.SYNC
        handle_sync_message(reply[1:], doc)
        yield ws, joined, doc


def _edit_bytes(doc: Doc, prefix: str) -> bytes:
    """Prepend ``prefix`` to the doc's first paragraph, return the update to
    send over the wire — mirrors test_coedit_checkpoint.py's ``_edit``, but
    returns bytes instead of applying via ``coedit.apply_update`` directly."""
    root = doc.get(ROOT_XML_KEY, type=XmlFragment)
    with doc.transaction():
        root.children[0].children[0].insert(0, prefix)
    return doc.get_update()


def _send_content(ws, update: bytes) -> None:
    ws.send_bytes(create_update_message(update))


def _send_awareness(ws, awareness: Awareness, state: dict) -> None:
    awareness.set_local_state(state)
    update = awareness.encode_awareness_update([awareness.client_id])
    ws.send_bytes(create_awareness_message(update))


def _recv_awareness(ws, into: Awareness) -> None:
    """Receive the next awareness frame (skipping JSON control frames) and
    apply it to ``into`` — a peer's own Awareness, so ``into.states`` then
    reflects the sender's just-broadcast state."""
    frame = _recv_bytes(ws)
    assert frame[0] == YMessageType.AWARENESS
    handle_awareness_frame(frame, into)


def handle_awareness_frame(frame: bytes, into: Awareness) -> None:
    payload = read_message(frame[1:])
    into.apply_awareness_update(payload, "remote")


def _ydoc_seq_at_least(session_id: int, n: int) -> bool:
    sess = coedit.get_session(session_id)
    return sess is not None and sess.ydoc_seq >= n


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

    with _ws(client) as (_ws_conn, joined, doc):
        assert joined["base_sha"] == sha
        assert joined["can_write"] is True
        assert [p["user_id"] for p in joined["participants"]] == [uid]
        # The document itself never travels in `joined` — it arrives via the
        # binary Yjs sync handshake `_ws` already completed by the time it
        # yields.
        assert reconstruct_body(doc) == "# Setup\n\nhello\n"


def test_connect_is_idempotent_and_shared(client):
    a = users_repo.create(email="ada@x.com", password="hunter2-x", name="Ada")
    b = users_repo.create(email="bo@x.com", password="hunter2-x", name="Bo")
    _seed_page()

    login_fastapi(client, a)
    with _ws(client) as (_ws_a, first, _doc_a):
        login_fastapi(client, b)
        with _ws(client) as (_ws_b, second, _doc_b):
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
    # per-frame (content update/checkpoint). A read-only user lands in the
    # roster; they never broadcast a caret, so presence renders them
    # "viewing" client-side.
    owner = users_repo.create(email="owner@x.com", password="hunter2-x", name="Owner")
    reader = users_repo.create(email="reader@x.com", password="hunter2-x", name="Reader")
    _seed_page("hello world")
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
    with _ws(client) as (ws, joined, doc):
        me = [p for p in joined["participants"] if p["user_id"] == reader]
        assert me and me[0]["last_edited_at"] is None
        assert joined["can_write"] is False

        # A content update from a read-only viewer is silently dropped —
        # _apply_yjs_frame returns None before ever calling
        # coedit.apply_update/touch, so there's no ack to assert on; proof
        # is that last_edited_at never advances. Checked *inside* this
        # block, before disconnect — closing the connection removes the
        # participant row entirely, so checking after would just find an
        # empty list, not a preserved None.
        _send_content(ws, _edit_bytes(doc, "x"))
        time.sleep(0.2)
        after = [p for p in coedit.list_participants(joined["session_id"]) if p.user_id == reader]
        assert after and after[0].last_edited_at is None

    # The read tells the frontend not to offer editing at all.
    doc_resp = client.get(f"/api/wiki/file?path={_PATH}")
    assert doc_resp.status_code == 200
    assert doc_resp.json()["can_write"] is False

    login_fastapi(client, owner)
    assert client.get(f"/api/wiki/file?path={_PATH}").json()["can_write"] is True


def test_op_stamps_last_edited_at(client):
    uid = users_repo.create(email="ada@x.com", password="hunter2-x", name="Ada")
    login_fastapi(client, uid)
    _seed_page("hello world")

    with _ws(client) as (ws, joined, doc):
        assert joined["participants"][0]["last_edited_at"] is None
        _send_content(ws, _edit_bytes(doc, "x"))
        _wait_for(
            lambda: any(
                p.last_edited_at is not None
                for p in coedit.list_participants(joined["session_id"])
                if p.user_id == uid
            )
        )
        after = coedit.list_participants(joined["session_id"])
        me = [p for p in after if p.user_id == uid]
        assert me and me[0].last_edited_at is not None


def test_disconnect_removes_participant(client):
    uid = users_repo.create(email="ada@x.com", password="hunter2-x", name="Ada")
    login_fastapi(client, uid)
    _seed_page()

    # immediate_mode: if teardown takes the cancellation path, the leave is
    # enqueued — inline mode runs it here instead of on a worker.
    with coedit_queue.immediate_mode():
        with _ws(client) as (_ws_conn, joined, _doc):
            sid = joined["session_id"]
            assert len(coedit.list_participants(sid)) == 1

        _wait_for(lambda: coedit.list_participants(sid) == [])
    assert coedit.list_participants(sid) == []


def test_disconnect_of_last_participant_checkpoints(client):
    uid = users_repo.create(email="ada@x.com", password="hunter2-x", name="Ada")
    login_fastapi(client, uid)
    _seed_page("hello world")

    with coedit_queue.immediate_mode():
        with _ws(client) as (ws, joined, doc):
            sid = joined["session_id"]
            _send_content(ws, _edit_bytes(doc, "EDITED "))
            _wait_for(lambda: _ydoc_seq_at_least(sid, 1))
        # The disconnect handler enqueues the checkpoint; immediate_mode runs
        # it inline wherever the handler executes. Wait *inside* the block:
        # once the flag drops, a late enqueue would go to the real queue.
        _wait_for(
            lambda: git.read_file(_PATH) == "EDITED hello world\n"
            and coedit.get_active_session(_PATH) is None
        )

    session_after = coedit.get_active_session(_PATH)
    assert git.read_file(_PATH) == "EDITED hello world\n", (
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
        with _ws(client) as (ws, joined, doc):
            sid = joined["session_id"]
            _send_content(ws, _edit_bytes(doc, "EDITED "))
            _wait_for(lambda: _ydoc_seq_at_least(sid, 1))
            ws.send_json({"type": "checkpoint", "request_id": "c"})
            result = _recv_typed_json(ws, "checkpoint_result")
            assert result == {"type": "checkpoint_result", "request_id": "c", "ok": True}
            # An explicit checkpoint doesn't close a session with an active
            # participant — must assert this before the connection closes;
            # disconnecting drops the last participant too, which (with an
            # already-clean buffer) triggers its own close.
            assert coedit.get_active_session(_PATH) is not None

    assert git.read_file(_PATH) == "EDITED hello world\n"
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
    with _ws(client) as (_owner_ws, _joined, _owner_doc):
        login_fastapi(client, other)
        with _ws(client) as (ws, _joined2, _doc):
            ws.send_json({"type": "checkpoint", "request_id": "c"})
            result = _recv_typed_json(ws, "checkpoint_result")
            assert result == {"type": "checkpoint_result", "request_id": "c", "ok": False}


def _login_and_join(client, email="ada@x.com"):
    uid = users_repo.create(email=email, password="hunter2-x", name="Ada")
    login_fastapi(client, uid)
    _seed_page("hello world")
    return client.websocket_connect(f"/api/coedit/ws?path={_PATH}")


def test_content_update_syncs_to_peer(client):
    # No per-message ack/version exists any more for a content edit (CRDT
    # updates just apply + broadcast — see app/api/coedit.py's
    # _apply_yjs_frame) — the observable contract is that a peer's own doc
    # picks up the change.
    a = users_repo.create(email="ada@x.com", password="hunter2-x", name="Ada")
    b = users_repo.create(email="bo@x.com", password="hunter2-x", name="Bo")
    _seed_page("hello world")

    login_fastapi(client, a)
    with _ws(client) as (ws_a, _joined_a, doc_a):
        login_fastapi(client, b)
        with _ws(client) as (ws_b, _joined_b, doc_b):
            ws_a.receive_json()  # the presence frame announcing b's join

            update = _edit_bytes(doc_a, "EDITED ")
            _send_content(ws_a, update)

            frame = _recv_bytes(ws_b)
            assert frame[0] == YMessageType.SYNC
            handle_sync_message(frame[1:], doc_b)
            assert reconstruct_body(doc_b) == "EDITED hello world\n"


def test_reconnect_receives_current_content_after_missed_edit(client):
    # Catch-up-on-reconnect is now intrinsic to the sync handshake itself
    # (a fresh client Doc always gets the room's *current* full state),
    # rather than a separate "missed ops since X" message — this is the
    # moral equivalent of the old get_ops-based rebase test.
    uid = users_repo.create(email="ada@x.com", password="hunter2-x", name="Ada")
    login_fastapi(client, uid)
    _seed_page("hello world")

    with _ws(client) as (ws, joined, doc):
        _send_content(ws, _edit_bytes(doc, "EDITED "))
        _wait_for(lambda: _ydoc_seq_at_least(joined["session_id"], 1))

    # Reconnect as a brand-new client (empty Doc, as if the page reloaded)
    # while the session is still active (no one else joined/left).
    with _ws(client) as (_ws2, _joined2, fresh_doc):
        assert reconstruct_body(fresh_doc) == "EDITED hello world\n"


def test_cursor_broadcasts_to_peers(client):
    a = users_repo.create(email="ada@x.com", password="hunter2-x", name="Ada")
    b = users_repo.create(email="bo@x.com", password="hunter2-x", name="Bo")
    _seed_page("hello world")

    login_fastapi(client, a)
    with _ws(client) as (ws_a, _joined_a, doc_a):
        login_fastapi(client, b)
        with _ws(client) as (ws_b, _joined_b, doc_b):
            ws_a.receive_json()  # the presence frame announcing b's join
            awareness_a = Awareness(doc_a)
            awareness_b = Awareness(doc_b)
            _send_awareness(ws_a, awareness_a, {"cursor": {"anchor": 0, "head": 5}})
            _recv_awareness(ws_b, awareness_b)
            assert awareness_b.states[awareness_a.client_id]["cursor"] == {"anchor": 0, "head": 5}


def test_cursor_clear_broadcasts_null_anchor(client):
    # A cleared local state (editor blur / tab hidden) is a caret clear —
    # broadcast to the session so peers drop the caret; nothing persisted.
    a = users_repo.create(email="ada@x.com", password="hunter2-x", name="Ada")
    b = users_repo.create(email="bo@x.com", password="hunter2-x", name="Bo")
    _seed_page("hello world")

    login_fastapi(client, a)
    with _ws(client) as (ws_a, _joined_a, doc_a):
        login_fastapi(client, b)
        with _ws(client) as (ws_b, _joined_b, doc_b):
            ws_a.receive_json()  # presence
            awareness_a = Awareness(doc_a)
            awareness_b = Awareness(doc_b)
            _send_awareness(ws_a, awareness_a, {"cursor": None})
            _recv_awareness(ws_b, awareness_b)
            assert awareness_b.states[awareness_a.client_id]["cursor"] is None


def test_cursor_requires_write(client):
    # See test_checkpoint_requires_write's comment: `other` needs read to
    # connect at all — granting only read (not write) is what exercises the
    # write-specific rejection this test is actually about.
    owner = users_repo.create(email="owner@x.com", password="hunter2-x", name="Owner")
    other = users_repo.create(email="other@x.com", password="hunter2-x", name="Other")
    _seed_page("hello world")
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
    with _ws(client) as (owner_ws, _joined, owner_doc):
        login_fastapi(client, other)
        with _ws(client) as (ws, _joined2, doc):
            owner_ws.receive_json()  # presence frame for other's join
            awareness = Awareness(doc)
            _send_awareness(ws, awareness, {"cursor": {"anchor": 0, "head": 0}})
            # Awareness is fire-and-forget (no result frame to assert on
            # directly — see app/api/coedit.py), so prove the rejection by
            # racing it against a legitimate, always-broadcast content
            # update from the owner: broadcasts (including a sender's own)
            # land on every connection in arrival order, so if `other`'s
            # connection sees the owner's update *before* any awareness
            # frame, their own awareness update was dropped rather than
            # merely reordered behind it.
            _send_content(owner_ws, _edit_bytes(owner_doc, "x"))
            first = ws.receive()
            assert first.get("bytes") is not None
            assert first["bytes"][0] == YMessageType.SYNC


def test_op_requires_write(client):
    # See test_checkpoint_requires_write's comment: `other` needs read to
    # connect at all — granting only read (not write) is what exercises the
    # write-specific rejection this test is actually about.
    owner = users_repo.create(email="owner@x.com", password="hunter2-x", name="Owner")
    other = users_repo.create(email="other@x.com", password="hunter2-x", name="Other")
    _seed_page("hello world")
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
    with _ws(client) as (_owner_ws, _joined, _owner_doc):
        login_fastapi(client, other)
        with _ws(client) as (ws, joined2, doc):
            _send_content(ws, _edit_bytes(doc, "x"))
            after = [
                p for p in coedit.list_participants(joined2["session_id"]) if p.user_id == other
            ]
            assert after and after[0].last_edited_at is None


def test_write_permission_revoked_mid_session_does_not_gate_open_connection_but_blocks_reconnect(client):
    # can_write is resolved once at connect time (_connect_sync) and closed
    # over for the connection's whole lifetime (app/api/coedit.py's own
    # module docstring calls this out as a known, accepted limitation — a
    # mid-session ACL change takes effect on the *next reconnect*, not the
    # next message). So a revoke doesn't retroactively gate an update sent
    # later on the same, already-open connection — but connecting fresh
    # afterward re-resolves can_write and does see it.
    owner = users_repo.create(email="owner@x.com", password="hunter2-x", name="Owner")
    editor = users_repo.create(email="editor@x.com", password="hunter2-x", name="Editor")
    _seed_page("hello world")
    acl.set_owner(_PATH, owner)
    # A separate, never-revoked read grant: "write" implies "read" (see
    # acl.effective) only while the write grant is active — revoking it
    # alone would otherwise leave editor with zero permissions at all,
    # failing the reconnect's own require_can("read", ...) gate (403)
    # before ever reaching the can_write resolution this test is about.
    acl.grant(
        resource_kind="page",
        resource_path=_PATH,
        principal_kind="user",
        principal_id=editor,
        permission="read",
        granted_by_user_id=owner,
    )
    entry_id = acl.grant(
        resource_kind="page",
        resource_path=_PATH,
        principal_kind="user",
        principal_id=editor,
        permission="write",
        granted_by_user_id=owner,
    )

    login_fastapi(client, editor)
    with _ws(client) as (ws, joined, doc):
        _send_content(ws, _edit_bytes(doc, "EDITED "))
        _wait_for(lambda: _ydoc_seq_at_least(joined["session_id"], 1))
        acl.revoke(entry_id)
        _send_content(ws, _edit_bytes(doc, "MORE "))
        _wait_for(lambda: _ydoc_seq_at_least(joined["session_id"], 2))
        after = coedit.get_session(joined["session_id"])
        assert after is not None and after.ydoc_seq == 2  # still applied — same open connection

    # A fresh connection re-resolves can_write against the now-revoked ACL.
    with _ws(client) as (ws2, joined2, doc2):
        assert joined2["can_write"] is False
        before = coedit.get_session(joined2["session_id"])
        _send_content(ws2, _edit_bytes(doc2, "BLOCKED "))
        time.sleep(0.2)
        after2 = coedit.get_session(joined2["session_id"])
        assert before is not None and after2 is not None
        assert after2.ydoc_seq == before.ydoc_seq  # dropped, unlike on the stale connection


def test_file_read_serves_live_buffer_during_session(client):
    # GET /wiki/file is session-aware: while a session is open, it serves the
    # live Postgres buffer, so an edit is visible immediately — no dependency on
    # the async checkpoint commit (git HEAD is unchanged here).
    uid = users_repo.create(email="ada@x.com", password="hunter2-x", name="Ada")
    login_fastapi(client, uid)
    sha = _seed_page("hello world")
    with _ws(client) as (ws, _joined, doc):
        _send_content(ws, _edit_bytes(doc, "LIVE "))
        # coedit_room.read_body_sync is a full reconstruct_body reserialize
        # (markdown_yjs.serialize_block always terminates a block with one
        # newline), so the live buffer ends in "\n" even though the
        # committed seed doesn't.
        _wait_for(
            lambda: client.get(f"/api/wiki/file?path={_PATH}").json()["body"]
            == "LIVE hello world\n"
        )

        resp = client.get(f"/api/wiki/file?path={_PATH}")
        assert resp.status_code == 200
        body = resp.json()
        assert body["body"] == "LIVE hello world\n"  # buffer, not committed HEAD
        assert body["head_sha"] == sha  # HEAD still the pre-session commit
        # git working tree is untouched — nothing was committed.
        assert git.read_file(_PATH) == "hello world"


def test_file_read_merges_agent_commit_over_live_buffer(client):
    # Safety net: when an agent commits to git after the session opened (HEAD
    # moves past base_sha), the read quick-merges the committed change over the
    # buffer so a viewer sees both the in-session edit and the agent's edit.
    uid = users_repo.create(email="ada@x.com", password="hunter2-x", name="Ada")
    login_fastapi(client, uid)
    doc_body = "one\ntwo\nthree\nfour\nfive\n"
    _seed_page(doc_body)
    with _ws(client) as (ws, _joined, doc):
        # Human edits the first line in the buffer...
        _send_content(ws, _edit_bytes(doc, "ONE-"))
        # ...an agent commits a distant, non-overlapping change out of band.
        git.commit_file(_PATH, "one\ntwo\nthree\nfour\nFIVE\n", message="agent", author="A <a@x.com>")

        _wait_for(
            lambda: client.get(f"/api/wiki/file?path={_PATH}").json()["body"]
            == "ONE-one\ntwo\nthree\nfour\nFIVE\n"
        )
        body = client.get(f"/api/wiki/file?path={_PATH}").json()["body"]
        assert body == "ONE-one\ntwo\nthree\nfour\nFIVE\n"  # both edits, no LLM, no commit


def test_file_read_serves_head_when_buffer_conflicts_with_committed_change(client):
    # When an agent commits a change that OVERLAPS the in-session edit (HEAD
    # moves past base_sha, the 3-way merge conflicts), the read serves committed
    # HEAD rather than the buffer. Preferring the buffer here would let a stale
    # session (in the limit, a zombie with no one left to reconcile it) hide the
    # committed change from every viewer — the 2026-07-06 incident.
    uid = users_repo.create(email="ada@x.com", password="hunter2-x", name="Ada")
    login_fastapi(client, uid)
    _seed_page("one\ntwo\nthree\n")
    with _ws(client) as (ws, _joined, doc):
        # Human edits the first line in the buffer...
        _send_content(ws, _edit_bytes(doc, "BUFFER-"))
        # ...an agent commits a CONFLICTING change to the same first line out of band.
        git.commit_file(_PATH, "AGENT\ntwo\nthree\n", message="agent", author="A <a@x.com>")

        _wait_for(
            lambda: client.get(f"/api/wiki/file?path={_PATH}").json()["body"] == "AGENT\ntwo\nthree\n"
        )
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
    _seed_page("hello world")
    with _ws(client) as (ws, joined, doc):
        _send_content(ws, _edit_bytes(doc, "LIVE "))
        _wait_for(
            lambda: client.get(f"/api/wiki/file?path={_PATH}").json()["body"]
            == "LIVE hello world\n"
        )
        coedit.leave(joined["session_id"], uid)  # everyone leaves; session stays active (zombie)
        st = coedit.get_active_session(_PATH)
        assert st is not None and st.status == "active"

        body = client.get(f"/api/wiki/file?path={_PATH}").json()["body"]
        assert body == "LIVE hello world\n"  # HEAD hasn't moved → buffer still safe to serve
