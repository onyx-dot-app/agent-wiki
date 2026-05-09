"""Tests for the chat sessions repo and the /api/chat/sessions HTTP routes."""
from __future__ import annotations

import pytest

from app.chat import sessions as repo


# --------------------------------------------------------------------------- #
# Repo                                                                        #
# --------------------------------------------------------------------------- #


@pytest.fixture
def two_users(tmp_db):
    from tests._seed import seed_user

    a = seed_user(uid="usr_a", email="a@x.com")
    b = seed_user(uid="usr_b", email="b@x.com")
    return a, b


def test_create_and_get_session(two_users):
    a, _ = two_users
    s = repo.create(a)
    assert s["id"] and s["title"] is None and s["user_id"] == a

    fetched = repo.get(s["id"], a)
    assert fetched is not None
    assert fetched["id"] == s["id"]


def test_get_is_owner_scoped(two_users):
    a, b = two_users
    s = repo.create(a)
    assert repo.get(s["id"], b) is None
    assert repo.get(s["id"], a) is not None


def test_list_orders_by_updated_at_desc(two_users):
    a, _ = two_users
    s1 = repo.create(a)
    s2 = repo.create(a)
    # Bump s1 so it sorts above s2.
    repo.touch(s1["id"])
    rows = repo.list_for_user(a)
    assert [r["id"] for r in rows] == [s1["id"], s2["id"]]


def test_append_messages_assigns_monotonic_ordering(two_users):
    a, _ = two_users
    s = repo.create(a)
    m1 = repo.append_message(s["id"], role="user", content="hi", events=None)
    m2 = repo.append_message(
        s["id"],
        role="assistant",
        content="hello",
        events=[{"type": "text_delta", "text": "hello"}],
    )
    m3 = repo.append_message(s["id"], role="user", content="more", events=None)
    assert m1["ordering"] == 0
    assert m2["ordering"] == 1
    assert m3["ordering"] == 2

    fetched = repo.get_messages(s["id"])
    assert [m["ordering"] for m in fetched] == [0, 1, 2]
    assert fetched[1]["events"] == [{"type": "text_delta", "text": "hello"}]
    assert fetched[0]["events"] is None


def test_append_message_rejects_invalid_role(two_users):
    a, _ = two_users
    s = repo.create(a)
    with pytest.raises(ValueError):
        repo.append_message(s["id"], role="system", content="x", events=None)


def test_delete_cascades_messages(two_users):
    a, _ = two_users
    s = repo.create(a)
    repo.append_message(s["id"], role="user", content="hi", events=None)
    repo.append_message(s["id"], role="assistant", content="ok", events=[])
    assert repo.count_messages(s["id"]) == 2

    assert repo.delete(s["id"], a) is True
    assert repo.get(s["id"], a) is None
    # Cascade dropped the message rows too.
    assert repo.get_messages(s["id"]) == []


def test_delete_is_owner_scoped(two_users):
    a, b = two_users
    s = repo.create(a)
    assert repo.delete(s["id"], b) is False
    assert repo.get(s["id"], a) is not None


def test_update_title_persists(two_users):
    a, _ = two_users
    s = repo.create(a)
    repo.update_title(s["id"], "An interesting chat")
    fetched = repo.get(s["id"], a)
    assert fetched is not None
    assert fetched["title"] == "An interesting chat"


# --------------------------------------------------------------------------- #
# API                                                                         #
# --------------------------------------------------------------------------- #


@pytest.fixture
def app(tmp_db):
    from app.main import create_app

    flask_app = create_app()
    flask_app.config["TESTING"] = True
    return flask_app


def _signed_in_client(app, email: str, password: str = "hunter2"):
    from app.auth import users as users_repo

    users_repo.create(email=email, password=password, name=email.split("@")[0])
    client = app.test_client()
    resp = client.post(
        "/api/auth/login", json={"email": email, "password": password}
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    return client


def test_session_crud_round_trip(app):
    client = _signed_in_client(app, "alice@example.com")

    # No sessions yet.
    resp = client.get("/api/chat/sessions")
    assert resp.status_code == 200
    assert resp.get_json() == []

    # Create.
    resp = client.post("/api/chat/sessions")
    assert resp.status_code == 201
    sess = resp.get_json()
    sid = sess["id"]
    assert sess["title"] is None

    # List sees it.
    rows = client.get("/api/chat/sessions").get_json()
    assert len(rows) == 1 and rows[0]["id"] == sid

    # Get returns empty messages.
    detail = client.get(f"/api/chat/sessions/{sid}").get_json()
    assert detail["session"]["id"] == sid
    assert detail["messages"] == []

    # Delete removes it.
    resp = client.delete(f"/api/chat/sessions/{sid}")
    assert resp.status_code == 204
    assert client.get(f"/api/chat/sessions/{sid}").status_code == 404


def test_sessions_are_per_user(app):
    a_client = _signed_in_client(app, "alice@example.com")
    b_client = _signed_in_client(app, "bob@example.com")

    a_sess = a_client.post("/api/chat/sessions").get_json()
    sid = a_sess["id"]

    # B can't list, get, or delete A's session.
    assert b_client.get("/api/chat/sessions").get_json() == []
    assert b_client.get(f"/api/chat/sessions/{sid}").status_code == 404
    assert b_client.delete(f"/api/chat/sessions/{sid}").status_code == 404


def test_send_message_persists_user_and_assistant_turn(app, monkeypatch):
    """Full SSE round-trip: user content + assistant final text + events end up in the DB."""
    client = _signed_in_client(app, "alice@example.com")

    def fake_stream(messages, *, model=None):
        yield {"type": "tool_call", "id": "c1", "name": "wiki_search", "arguments": {"q": "x"}}
        yield {"type": "tool_result", "id": "c1", "name": "wiki_search", "content": "[]"}
        yield {"type": "iteration_done"}
        yield {"type": "text_delta", "text": "Found "}
        yield {"type": "text_delta", "text": "nothing."}
        yield {"type": "done"}

    monkeypatch.setattr("app.api.chat.run_chat_stream", fake_stream)
    # Don't actually run title generation in-thread during the test —
    # the immediate-mode default would, and that pulls in client.complete.
    monkeypatch.setattr("app.api.chat.generate_chat_title", lambda *a, **k: None)

    sid = client.post("/api/chat/sessions").get_json()["id"]
    resp = client.post(
        "/api/chat/messages",
        json={"session_id": sid, "content": "search the wiki"},
    )
    assert resp.status_code == 200
    # Drain the response body so the generator runs end-to-end (Flask's
    # test client buffers streaming responses; reading the data forces
    # generator completion which is when our persistence runs).
    resp.get_data(as_text=True)

    detail = client.get(f"/api/chat/sessions/{sid}").get_json()
    msgs = detail["messages"]
    assert len(msgs) == 2
    assert msgs[0]["role"] == "user"
    assert msgs[0]["content"] == "search the wiki"
    assert msgs[0]["events"] is None
    assert msgs[1]["role"] == "assistant"
    assert msgs[1]["content"] == "Found nothing."
    # Events captured for re-rendering — including the tool call.
    types = [e["type"] for e in msgs[1]["events"]]
    assert "tool_call" in types and "done" in types


def test_send_message_does_not_persist_assistant_on_llm_error(app, monkeypatch):
    """If the LLM blows up mid-stream, only the user turn lands in the DB."""
    from app.llm.errors import LLMError

    client = _signed_in_client(app, "alice@example.com")

    def fake_stream(messages, *, model=None):
        yield {"type": "text_delta", "text": "partial"}
        raise LLMError("rate_limit", "boom")

    monkeypatch.setattr("app.api.chat.run_chat_stream", fake_stream)
    monkeypatch.setattr("app.api.chat.generate_chat_title", lambda *a, **k: None)

    sid = client.post("/api/chat/sessions").get_json()["id"]
    resp = client.post(
        "/api/chat/messages",
        json={"session_id": sid, "content": "hi"},
    )
    resp.get_data(as_text=True)

    msgs = client.get(f"/api/chat/sessions/{sid}").get_json()["messages"]
    assert [m["role"] for m in msgs] == ["user"]


def test_send_message_enqueues_title_generation_only_on_first_turn(app, monkeypatch):
    client = _signed_in_client(app, "alice@example.com")

    def fake_stream(messages, *, model=None):
        yield {"type": "text_delta", "text": "ok"}
        yield {"type": "done"}

    calls: list[str] = []
    monkeypatch.setattr("app.api.chat.run_chat_stream", fake_stream)
    monkeypatch.setattr(
        "app.api.chat.generate_chat_title",
        lambda session_id: calls.append(session_id),
    )

    sid = client.post("/api/chat/sessions").get_json()["id"]
    client.post(
        "/api/chat/messages", json={"session_id": sid, "content": "first"}
    ).get_data(as_text=True)
    client.post(
        "/api/chat/messages", json={"session_id": sid, "content": "second"}
    ).get_data(as_text=True)

    assert calls == [sid]
