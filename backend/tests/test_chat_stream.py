"""Tests for the streaming chat agent loop and the SSE chat endpoint.

These pin two behaviors that are easy to regress:

* ``run_chat_loop_stream`` re-enters the model after each tool dispatch
  (multi-iteration tool use) and only emits a terminal ``done`` event once
  the model returns a turn with no tool calls.
* The Flask blueprint emits SSE frames in the same order, terminating with
  ``done``, and surfaces ``LLMError`` as a final ``error`` event.
"""
from __future__ import annotations

import json

import pytest

from app.llm import client as llm_client
from app.llm.agents import chat as chat_agent
from app.llm.errors import LLMError


@pytest.fixture
def stub_stream(monkeypatch):
    """Replace ``client.stream`` with a queued sequence of event lists.

    Each call consumes the next list. Lets a test script the model side of a
    tool-using conversation without going near the real SDKs.
    """
    queue: list[list[dict]] = []

    def install(scripted: list[list[dict]]):
        queue.extend(scripted)

    def fake_stream(messages, *, model=None, tools=None, max_tokens=4096):
        if not queue:
            raise AssertionError("stub_stream exhausted but client.stream was called")
        events = queue.pop(0)
        for ev in events:
            yield ev

    monkeypatch.setattr(llm_client, "stream", fake_stream)
    monkeypatch.setattr(chat_agent.client, "stream", fake_stream)
    return install


def _done():
    return {
        "type": "done",
        "stop_reason": "end_turn",
        "usage": {"input_tokens": 1, "output_tokens": 1},
    }


def test_loop_stream_multi_iteration_tool_use(stub_stream):
    """Iteration 1: model calls a tool. Iteration 2: model emits final text."""

    stub_stream(
        [
            # Iteration 1 — tool call only
            [
                {"type": "tool_call", "id": "call_1", "name": "echo", "arguments": {"x": 1}},
                _done(),
            ],
            # Iteration 2 — final answer
            [
                {"type": "text_delta", "text": "got "},
                {"type": "text_delta", "text": "1"},
                _done(),
            ],
        ]
    )

    messages: list[dict] = [
        {"role": "system", "content": "be terse"},
        {"role": "user", "content": "use tool"},
    ]

    events = list(
        chat_agent.run_chat_loop_stream(
            messages,
            tools=[{"name": "echo", "description": "", "input_schema": {"type": "object"}}],
            tool_dispatch=lambda name, args: {"echo": args},
        )
    )

    types = [e["type"] for e in events]
    assert types == [
        "tool_call",
        "tool_result",
        "iteration_done",
        "text_delta",
        "text_delta",
        "done",
    ]

    # The conversation list should now contain: system, user, assistant
    # (with tool_calls), tool result, assistant final.
    roles = [m["role"] for m in messages]
    assert roles == ["system", "user", "assistant", "tool", "assistant"]
    assert messages[2]["tool_calls"] == [
        {"id": "call_1", "name": "echo", "arguments": {"x": 1}}
    ]
    assert messages[3] == {
        "role": "tool",
        "tool_call_id": "call_1",
        "content": json.dumps({"echo": {"x": 1}}),
    }
    assert messages[4]["content"] == "got 1"


def test_loop_stream_tool_dispatch_errors_are_surfaced_to_model(stub_stream):
    stub_stream(
        [
            [
                {"type": "tool_call", "id": "call_1", "name": "bad", "arguments": {}},
                _done(),
            ],
            [{"type": "text_delta", "text": "ok"}, _done()],
        ]
    )

    def boom(name, args):
        raise RuntimeError("nope")

    messages: list[dict] = [{"role": "user", "content": "go"}]

    list(
        chat_agent.run_chat_loop_stream(
            messages,
            tools=[{"name": "bad", "description": "", "input_schema": {"type": "object"}}],
            tool_dispatch=boom,
        )
    )

    tool_msg = next(m for m in messages if m["role"] == "tool")
    assert json.loads(tool_msg["content"]) == {"error": "nope"}


def test_loop_populates_seen_paths_from_read_page_results(stub_stream):
    """``read_page`` feeds the target ``path`` into ``seen_doc_paths``;
    ``search_wiki`` snippets do NOT count as a read."""
    from app.llm.agents._session import seen_doc_paths

    # Iter 1: search_wiki (snippets only — must NOT count as read).
    # Iter 2: read_page on guide.md (counts).
    # Iter 3: peek captures seen-set contents.
    # Iter 4: text + done.
    stub_stream(
        [
            [
                {"type": "tool_call", "id": "c1", "name": "search_wiki",
                 "arguments": {"query": "x"}},
                _done(),
            ],
            [
                {"type": "tool_call", "id": "c2", "name": "read_page",
                 "arguments": {"path": "guide.md"}},
                _done(),
            ],
            [
                {"type": "tool_call", "id": "c3", "name": "peek", "arguments": {}},
                _done(),
            ],
            [{"type": "text_delta", "text": "ok"}, _done()],
        ]
    )

    captured: dict = {}

    def dispatch(name, args):
        if name == "search_wiki":
            return {
                "results": [
                    {"path": "auth/passwords.md", "title": "pw", "snippet": "..."},
                    {"path": "guide.md", "title": "Guide", "snippet": "..."},
                ]
            }
        if name == "read_page":
            return {"path": args["path"], "title": "Guide", "body": "# Guide\n"}
        if name == "peek":
            captured["seen"] = set(seen_doc_paths.get() or [])
            return {}
        return {}

    list(
        chat_agent.run_chat_loop_stream(
            [{"role": "user", "content": "go"}],
            tools=[
                {"name": "search_wiki", "description": "",
                 "input_schema": {"type": "object"}},
                {"name": "read_page", "description": "",
                 "input_schema": {"type": "object"}},
                {"name": "peek", "description": "",
                 "input_schema": {"type": "object"}},
            ],
            tool_dispatch=dispatch,
        )
    )

    # Only read_page's path is in seen — search_wiki paths are NOT.
    assert captured["seen"] == {"guide.md"}
    # Outside the loop the ContextVar resets to default (None).
    assert seen_doc_paths.get() is None


def test_loop_stream_max_iterations_raises(stub_stream):
    """If the model keeps emitting tool calls, the loop should hard-stop."""
    # Schedule far more iterations than max.
    stub_stream(
        [
            [
                {"type": "tool_call", "id": f"c{i}", "name": "e", "arguments": {}},
                _done(),
            ]
            for i in range(5)
        ]
    )

    with pytest.raises(RuntimeError, match="did not terminate"):
        list(
            chat_agent.run_chat_loop_stream(
                [{"role": "user", "content": "go"}],
                tools=[{"name": "e", "description": "", "input_schema": {"type": "object"}}],
                tool_dispatch=lambda n, a: "",
                max_iterations=3,
            )
        )


# --------------------------------------------------------------------------- #
# SSE endpoint
# --------------------------------------------------------------------------- #


@pytest.fixture
def app(tmp_db, monkeypatch):
    from app.main import create_app

    flask_app = create_app()
    flask_app.config["TESTING"] = True
    return flask_app


@pytest.fixture
def signed_in_client(app, tmp_db):
    """Create a user, log in, and hand back a Flask test client."""
    from app.auth import users as users_repo

    users_repo.create(email="t@example.com", password="hunter2", name="t")

    client = app.test_client()
    resp = client.post(
        "/api/auth/login",
        json={"email": "t@example.com", "password": "hunter2"},
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)
    return client


def _parse_sse(body: str) -> list[dict]:
    """Pull the JSON payload out of every ``data:`` line."""
    out: list[dict] = []
    for frame in body.split("\n\n"):
        data = "\n".join(
            line[5:].lstrip() for line in frame.split("\n") if line.startswith("data:")
        )
        if data:
            out.append(json.loads(data))
    return out


def _create_session(client) -> str:
    resp = client.post("/api/chat/sessions")
    assert resp.status_code == 201, resp.get_data(as_text=True)
    return resp.get_json()["id"]


def test_sse_endpoint_streams_text_then_done(signed_in_client, monkeypatch):
    def fake_stream(messages, *, model=None):
        yield {"type": "text_delta", "text": "hi "}
        yield {"type": "text_delta", "text": "there"}
        yield {"type": "done"}

    monkeypatch.setattr("app.api.chat.run_chat_stream", fake_stream)

    sid = _create_session(signed_in_client)
    resp = signed_in_client.post(
        "/api/chat/messages",
        json={"session_id": sid, "content": "hello"},
    )

    assert resp.status_code == 200
    assert resp.mimetype == "text/event-stream"
    events = _parse_sse(resp.get_data(as_text=True))
    assert events == [
        {"type": "text_delta", "text": "hi "},
        {"type": "text_delta", "text": "there"},
        {"type": "done"},
    ]


def test_sse_endpoint_emits_error_event_on_llm_error(signed_in_client, monkeypatch):
    def fake_stream(messages, *, model=None):
        # Yield one delta to prove a partial stream still terminates with an
        # error event (the frontend uses that to drop the empty placeholder).
        yield {"type": "text_delta", "text": "partial"}
        raise LLMError("rate_limit", "Anthropic rate limit hit. Please retry in a moment.")

    monkeypatch.setattr("app.api.chat.run_chat_stream", fake_stream)

    sid = _create_session(signed_in_client)
    resp = signed_in_client.post(
        "/api/chat/messages",
        json={"session_id": sid, "content": "hello"},
    )

    assert resp.status_code == 200
    events = _parse_sse(resp.get_data(as_text=True))
    assert events[0] == {"type": "text_delta", "text": "partial"}
    assert events[-1] == {
        "type": "error",
        "code": "rate_limit",
        "message": "Anthropic rate limit hit. Please retry in a moment.",
    }


def test_sse_endpoint_validates_request_body(signed_in_client):
    # Missing session_id → 400 with JSON envelope (NOT an SSE error event).
    resp = signed_in_client.post("/api/chat/messages", json={"content": "hi"})
    assert resp.status_code == 400
    assert resp.is_json
    assert "error" in resp.get_json()


def test_sse_endpoint_rejects_unknown_session(signed_in_client):
    resp = signed_in_client.post(
        "/api/chat/messages",
        json={"session_id": "no-such-session", "content": "hi"},
    )
    assert resp.status_code == 404
    assert "error" in resp.get_json()
