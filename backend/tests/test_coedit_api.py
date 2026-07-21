"""Co-edit HTTP surface (app/api/coedit.py) — join / leave and their
permission gating. The SSE stream's live delivery is covered at the channel
level in test_coedit_channel.py; here we exercise the request layer.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

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


def test_join_requires_auth(client):
    assert client.post("/api/coedit/join", json={"path": _PATH}).status_code == 401


def test_join_creates_session_seeded_from_head(client):
    uid = users_repo.create(email="ada@x.com", password="hunter2-x", name="Ada")
    login_fastapi(client, uid)
    sha = _seed_page()

    resp = client.post("/api/coedit/join", json={"path": _PATH})
    assert resp.status_code == 200
    body = resp.json()
    assert body["version"] == 0
    assert body["buffer"] == "# Setup\n\nhello\n"
    assert body["base_sha"] == sha
    assert [p["user_id"] for p in body["participants"]] == [uid]


def test_join_is_idempotent_and_shared(client):
    a = users_repo.create(email="ada@x.com", password="hunter2-x", name="Ada")
    b = users_repo.create(email="bo@x.com", password="hunter2-x", name="Bo")
    _seed_page()

    login_fastapi(client, a)
    first = client.post("/api/coedit/join", json={"path": _PATH}).json()
    login_fastapi(client, b)
    second = client.post("/api/coedit/join", json={"path": _PATH}).json()

    # Same shared session; both users are participants.
    assert first["session_id"] == second["session_id"]
    assert {p["user_id"] for p in second["participants"]} == {a, b}


def test_join_without_read_is_forbidden(client):
    owner = users_repo.create(email="owner@x.com", password="hunter2-x", name="Owner")
    other = users_repo.create(email="other@x.com", password="hunter2-x", name="Other")
    _seed_page()
    # Committing via git directly bypasses the lifecycle hook, so the page has
    # no ACL rows — setting an owner makes it owner-only (no public grant).
    acl.set_owner(_PATH, owner)

    login_fastapi(client, other)
    assert client.post("/api/coedit/join", json={"path": _PATH}).status_code == 403


def test_stream_requires_read(client):
    # Opening the stream joins the roster (page-open presence), gated on read
    # — symmetric with /join; writes are gated at /op. require_can raises
    # before the response starts streaming, so this returns 403 without
    # hanging.
    owner = users_repo.create(email="owner@x.com", password="hunter2-x", name="Owner")
    other = users_repo.create(email="other@x.com", password="hunter2-x", name="Other")
    _seed_page()
    acl.set_owner(_PATH, owner)  # owner-only page

    login_fastapi(client, owner)
    sid = client.post("/api/coedit/join", json={"path": _PATH}).json()["session_id"]

    login_fastapi(client, other)
    assert client.get(f"/api/coedit/stream?session_id={sid}").status_code == 403


def test_read_only_user_joins_as_viewer_but_cannot_edit(client):
    # Joining is page-open presence (read-gated); the write boundary is /op
    # and /cursor. A read-only user lands in the roster with caret_active
    # false — presence renders them "viewing".
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
    resp = client.post("/api/coedit/join", json={"path": _PATH})
    assert resp.status_code == 200
    sid = resp.json()["session_id"]
    me = [p for p in resp.json()["participants"] if p["user_id"] == reader]
    assert me and me[0]["caret_active"] is False

    op = client.post(
        "/api/coedit/op",
        json={
            "session_id": sid,
            "base_version": 0,
            "changes": [{"from": 0, "to": 0, "insert": "x"}],
        },
    )
    assert op.status_code == 403
    cursor = client.post(
        "/api/coedit/cursor",
        json={"session_id": sid, "anchor": 0, "head": 0, "typing": False},
    )
    assert cursor.status_code == 403

    # The read tells the frontend not to offer editing at all.
    doc = client.get(f"/api/wiki/file?path={_PATH}")
    assert doc.status_code == 200
    assert doc.json()["can_write"] is False

    login_fastapi(client, owner)
    assert client.get(f"/api/wiki/file?path={_PATH}").json()["can_write"] is True


def test_op_stamps_last_edited_at_and_caret(client):
    # An applied edit marks the caret active even without a cursor ping.
    uid = users_repo.create(email="ada@x.com", password="hunter2-x", name="Ada")
    login_fastapi(client, uid)
    _seed_page()

    joined = client.post("/api/coedit/join", json={"path": _PATH}).json()
    sid = joined["session_id"]
    assert joined["participants"][0]["last_edited_at"] is None
    assert joined["participants"][0]["caret_active"] is False

    _apply_op(client, sid, 0, [{"from": 0, "to": 0, "insert": "x"}])
    after = client.get(f"/api/coedit/session?session_id={sid}").json()
    me = [p for p in after["participants"] if p["user_id"] == uid]
    assert me and me[0]["last_edited_at"] is not None
    assert me[0]["caret_active"] is True


def test_leave_removes_participant(client):
    uid = users_repo.create(email="ada@x.com", password="hunter2-x", name="Ada")
    login_fastapi(client, uid)
    _seed_page()

    sid = client.post("/api/coedit/join", json={"path": _PATH}).json()["session_id"]
    assert len(coedit.list_participants(sid)) == 1

    resp = client.post("/api/coedit/leave", json={"session_id": sid})
    assert resp.status_code == 200
    assert coedit.list_participants(sid) == []


def test_leave_last_participant_checkpoints(client):
    uid = users_repo.create(email="ada@x.com", password="hunter2-x", name="Ada")
    login_fastapi(client, uid)
    _seed_page("hello world")
    sid = client.post("/api/coedit/join", json={"path": _PATH}).json()["session_id"]
    client.post(
        "/api/coedit/op",
        json={"session_id": sid, "base_version": 0, "changes": [{"from": 0, "to": 5, "insert": "hi"}]},
    )

    # The last leave enqueues a checkpoint; immediate_mode runs it inline.
    with coedit_queue.immediate_mode():
        assert client.post("/api/coedit/leave", json={"session_id": sid}).status_code == 200

    assert git.read_file(_PATH) == "hi world"
    assert coedit.get_active_session(_PATH) is None


def test_checkpoint_endpoint_commits_buffer(client):
    uid = users_repo.create(email="ada@x.com", password="hunter2-x", name="Ada")
    login_fastapi(client, uid)
    _seed_page("hello world")
    sid = client.post("/api/coedit/join", json={"path": _PATH}).json()["session_id"]
    client.post(
        "/api/coedit/op",
        json={"session_id": sid, "base_version": 0, "changes": [{"from": 0, "to": 5, "insert": "hi"}]},
    )

    with coedit_queue.immediate_mode():
        resp = client.post("/api/coedit/checkpoint", json={"session_id": sid})
    assert resp.status_code == 200
    assert resp.json() == {"queued": True}
    assert git.read_file(_PATH) == "hi world"
    # An explicit checkpoint doesn't close a session with an active participant.
    assert coedit.get_active_session(_PATH) is not None


def test_checkpoint_requires_write(client):
    owner = users_repo.create(email="owner@x.com", password="hunter2-x", name="Owner")
    other = users_repo.create(email="other@x.com", password="hunter2-x", name="Other")
    _seed_page()
    acl.set_owner(_PATH, owner)  # owner-only
    login_fastapi(client, owner)
    sid = client.post("/api/coedit/join", json={"path": _PATH}).json()["session_id"]

    login_fastapi(client, other)
    assert client.post("/api/coedit/checkpoint", json={"session_id": sid}).status_code == 403


def _login_and_join(client, email="ada@x.com") -> int:
    uid = users_repo.create(email=email, password="hunter2-x", name="Ada")
    login_fastapi(client, uid)
    _seed_page("hello world")
    return client.post("/api/coedit/join", json={"path": _PATH}).json()["session_id"]


def test_op_applies_and_returns_version(client):
    sid = _login_and_join(client)  # buffer seeded as "hello world"
    resp = client.post(
        "/api/coedit/op",
        json={"session_id": sid, "base_version": 0, "changes": [{"from": 0, "to": 5, "insert": "hi"}]},
    )
    assert resp.status_code == 200
    assert resp.json()["version"] == 1
    # GET /session reflects the applied edit.
    state = client.get(f"/api/coedit/session?session_id={sid}").json()
    assert state["version"] == 1
    assert state["buffer"] == "hi world"


def test_op_stale_base_version_is_409(client):
    sid = _login_and_join(client)
    client.post("/api/coedit/op", json={"session_id": sid, "base_version": 0, "changes": [{"from": 0, "to": 0, "insert": "x"}]})
    resp = client.post(
        "/api/coedit/op",
        json={"session_id": sid, "base_version": 0, "changes": [{"from": 0, "to": 0, "insert": "y"}]},
    )
    assert resp.status_code == 409


def test_op_out_of_bounds_is_422(client):
    sid = _login_and_join(client)
    resp = client.post(
        "/api/coedit/op",
        json={"session_id": sid, "base_version": 0, "changes": [{"from": 0, "to": 9999, "insert": "x"}]},
    )
    assert resp.status_code == 422


def test_op_malformed_change_is_rejected(client):
    # Missing 'to' fails Change request-body validation → the app's handler
    # returns 400 (semantic out-of-bounds is the 422 case above).
    sid = _login_and_join(client)
    resp = client.post(
        "/api/coedit/op",
        json={"session_id": sid, "base_version": 0, "changes": [{"from": 0, "insert": "x"}]},
    )
    assert resp.status_code == 400


def test_cursor_broadcasts_and_returns_ok(client):
    sid = _login_and_join(client)
    resp = client.post(
        "/api/coedit/cursor",
        json={"session_id": sid, "anchor": 0, "head": 5, "typing": True},
    )
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


def test_cursor_sets_and_clears_caret_active(client):
    # Placing a caret flips the participant to "editing" (caret_active true);
    # a null-position cursor (editor blur / tab hidden) clears it back — the
    # roster carries the state so late joiners label presence correctly.
    sid = _login_and_join(client)

    client.post(
        "/api/coedit/cursor",
        json={"session_id": sid, "anchor": 3, "head": 3, "typing": False},
    )
    roster = client.get(f"/api/coedit/session?session_id={sid}").json()["participants"]
    assert [p["caret_active"] for p in roster] == [True]

    cleared = client.post(
        "/api/coedit/cursor",
        json={"session_id": sid, "anchor": None, "head": None, "typing": False},
    )
    assert cleared.status_code == 200
    roster = client.get(f"/api/coedit/session?session_id={sid}").json()["participants"]
    assert [p["caret_active"] for p in roster] == [False]


def test_cursor_requires_write(client):
    owner = users_repo.create(email="owner@x.com", password="hunter2-x", name="Owner")
    other = users_repo.create(email="other@x.com", password="hunter2-x", name="Other")
    _seed_page()
    acl.set_owner(_PATH, owner)  # owner-only
    login_fastapi(client, owner)
    sid = client.post("/api/coedit/join", json={"path": _PATH}).json()["session_id"]

    login_fastapi(client, other)
    resp = client.post(
        "/api/coedit/cursor", json={"session_id": sid, "anchor": 0, "head": 0, "typing": False}
    )
    assert resp.status_code == 403


def test_op_requires_write(client):
    owner = users_repo.create(email="owner@x.com", password="hunter2-x", name="Owner")
    other = users_repo.create(email="other@x.com", password="hunter2-x", name="Other")
    _seed_page()
    acl.set_owner(_PATH, owner)  # owner-only
    login_fastapi(client, owner)
    sid = client.post("/api/coedit/join", json={"path": _PATH}).json()["session_id"]

    login_fastapi(client, other)
    resp = client.post(
        "/api/coedit/op",
        json={"session_id": sid, "base_version": 0, "changes": [{"from": 0, "to": 0, "insert": "x"}]},
    )
    assert resp.status_code == 403


def _apply_op(client, sid: int, base_version: int, changes: list[dict]) -> int:
    resp = client.post(
        "/api/coedit/op",
        json={"session_id": sid, "base_version": base_version, "changes": changes},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["version"]


def test_file_read_serves_live_buffer_during_session(client):
    # GET /wiki/file is session-aware: while a session is open, it serves the
    # live Postgres buffer, so an edit is visible immediately — no dependency on
    # the async checkpoint commit (git HEAD is unchanged here).
    uid = users_repo.create(email="ada@x.com", password="hunter2-x", name="Ada")
    login_fastapi(client, uid)
    sha = _seed_page("# Setup\n\nhello\n")
    sid = client.post("/api/coedit/join", json={"path": _PATH}).json()["session_id"]
    _apply_op(client, sid, 0, [{"from": 0, "to": len("# Setup\n\nhello\n"), "insert": "LIVE\n"}])

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
    sid = client.post("/api/coedit/join", json={"path": _PATH}).json()["session_id"]
    # Human edits the first line in the buffer...
    _apply_op(client, sid, 0, [{"from": 0, "to": 3, "insert": "ONE"}])
    # ...an agent commits a distant, non-overlapping change out of band.
    git.commit_file(_PATH, "one\ntwo\nthree\nfour\nFIVE\n", message="agent", author="A <a@x.com>")

    body = client.get(f"/api/wiki/file?path={_PATH}").json()["body"]
    assert body == "ONE\ntwo\nthree\nfour\nFIVE\n"  # both edits, no LLM, no commit


def test_ops_requires_auth(client):
    assert client.get("/api/coedit/ops?session_id=1&since_version=0").status_code == 401


def test_ops_since_returns_missed_changes_for_rebase(client):
    uid = users_repo.create(email="ada@x.com", password="hunter2-x", name="Ada")
    login_fastapi(client, uid)
    _seed_page("abcdef\n")
    sid = client.post("/api/coedit/join", json={"path": _PATH}).json()["session_id"]
    v1 = _apply_op(client, sid, 0, [{"from": 0, "to": 0, "insert": "X"}])
    v2 = _apply_op(client, sid, v1, [{"from": 0, "to": 0, "insert": "Y"}])

    # since_version=0 → both ops, oldest first, wire-shaped like op frames ("from" alias).
    body = client.get(f"/api/coedit/ops?session_id={sid}&since_version=0").json()
    assert body["current_head_version"] == v2
    assert [o["version"] for o in body["ops"]] == [v1, v2]
    assert body["ops"][0]["changes"] == [{"from": 0, "to": 0, "insert": "X"}]
    assert body["ops"][0]["author"] == uid

    # since_version=v1 → only the op after it.
    body2 = client.get(f"/api/coedit/ops?session_id={sid}&since_version={v1}").json()
    assert [o["version"] for o in body2["ops"]] == [v2]

    # since_version=head → nothing missed.
    assert client.get(f"/api/coedit/ops?session_id={sid}&since_version={v2}").json()["ops"] == []


def test_ops_404_when_no_active_session(client):
    uid = users_repo.create(email="ada@x.com", password="hunter2-x", name="Ada")
    login_fastapi(client, uid)
    assert client.get("/api/coedit/ops?session_id=99999&since_version=0").status_code == 404


def test_op_client_id_round_trips_to_ops(client):
    uid = users_repo.create(email="ada@x.com", password="hunter2-x", name="Ada")
    login_fastapi(client, uid)
    _seed_page("abc\n")
    sid = client.post("/api/coedit/join", json={"path": _PATH}).json()["session_id"]
    # Op tagged with a per-connection client id.
    resp = client.post(
        "/api/coedit/op",
        json={
            "session_id": sid,
            "base_version": 0,
            "changes": [{"from": 0, "to": 0, "insert": "X"}],
            "client_id": "cli_abc",
        },
    )
    assert resp.status_code == 200
    op = client.get(f"/api/coedit/ops?session_id={sid}&since_version=0").json()["ops"][0]
    assert op["client_id"] == "cli_abc"

    # Omitting client_id (non-collab client) is fine — it's null.
    resp2 = client.post(
        "/api/coedit/op",
        json={"session_id": sid, "base_version": 1, "changes": [{"from": 0, "to": 0, "insert": "Y"}]},
    )
    assert resp2.status_code == 200
    ops = client.get(f"/api/coedit/ops?session_id={sid}&since_version=1").json()["ops"]
    assert ops[0]["client_id"] is None


def test_file_read_serves_head_when_buffer_conflicts_with_committed_change(client):
    # When an agent commits a change that OVERLAPS the in-session edit (HEAD
    # moves past base_sha, the 3-way merge conflicts), the read serves committed
    # HEAD rather than the buffer. Preferring the buffer here would let a stale
    # session (in the limit, a zombie with no one left to reconcile it) hide the
    # committed change from every viewer — the 2026-07-06 incident.
    uid = users_repo.create(email="ada@x.com", password="hunter2-x", name="Ada")
    login_fastapi(client, uid)
    _seed_page("one\ntwo\nthree\n")
    sid = client.post("/api/coedit/join", json={"path": _PATH}).json()["session_id"]
    # Human edits the first line in the buffer...
    _apply_op(client, sid, 0, [{"from": 0, "to": 3, "insert": "BUFFER"}])
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
    sid = client.post("/api/coedit/join", json={"path": _PATH}).json()["session_id"]
    _apply_op(client, sid, 0, [{"from": 0, "to": len("# Setup\n\nhello\n"), "insert": "LIVE\n"}])
    coedit.leave(sid, uid)  # everyone leaves; session stays active (zombie)
    st = coedit.get_active_session(_PATH)
    assert st is not None and st.status == "active"

    body = client.get(f"/api/wiki/file?path={_PATH}").json()["body"]
    assert body == "LIVE\n"  # HEAD hasn't moved → buffer still safe to serve
