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


def test_force_final_answer_strips_tools_and_injects_reminder(stub_stream, monkeypatch):
    """On the last cycle, ``client.stream`` should be called with ``tools=None``
    and a user ``<system-reminder>`` message should be appended just before it."""
    captured_tools: list = []
    captured_messages_len: list[int] = []

    def fake_stream(messages, *, model=None, tools=None, max_tokens=4096):
        captured_tools.append(tools)
        captured_messages_len.append(len(messages))
        # Cycle 1: keep calling tools; Cycle 2 (final): emit text.
        if len(captured_tools) == 1:
            yield {"type": "tool_call", "id": "c1", "name": "e", "arguments": {}}
            yield _done()
            return
        yield {"type": "text_delta", "text": "best-effort answer"}
        yield _done()

    monkeypatch.setattr(chat_agent.client, "stream", fake_stream)

    messages: list[dict] = [{"role": "user", "content": "go"}]
    events = list(
        chat_agent.run_chat_loop_stream(
            messages,
            tools=[{"name": "e", "description": "", "input_schema": {"type": "object"}}],
            tool_dispatch=lambda n, a: "ok",
            max_iterations=2,
            force_final_answer=True,
        )
    )

    # First cycle had tools; last cycle had none.
    assert captured_tools[0] is not None
    assert captured_tools[1] is None

    # The reminder was injected as a user message just before the final cycle.
    reminder_msg = messages[-2]  # final assistant turn is the last entry
    assert reminder_msg["role"] == "user"
    assert reminder_msg["content"] == chat_agent.FINAL_CYCLE_REMINDER

    # And it should end on a clean `done`, not a RuntimeError.
    assert events[-1]["type"] == "done"
    assert messages[-1] == {"role": "assistant", "content": "best-effort answer"}


def test_force_final_answer_off_by_default_still_raises(stub_stream):
    """Default behavior is unchanged — exhausting iterations raises."""
    stub_stream(
        [
            [{"type": "tool_call", "id": f"c{i}", "name": "e", "arguments": {}}, _done()]
            for i in range(3)
        ]
    )

    with pytest.raises(RuntimeError, match="did not terminate"):
        list(
            chat_agent.run_chat_loop_stream(
                [{"role": "user", "content": "go"}],
                tools=[{"name": "e", "description": "", "input_schema": {"type": "object"}}],
                tool_dispatch=lambda n, a: "",
                max_iterations=2,
                # force_final_answer left at default False
            )
        )


def test_force_final_answer_no_op_when_loop_finishes_early(stub_stream):
    """If the model emits a final answer before the last cycle, the reminder
    must NOT be injected — force_final_answer only kicks in at the boundary."""
    stub_stream(
        [
            # Cycle 1: final answer immediately.
            [{"type": "text_delta", "text": "done early"}, _done()],
        ]
    )

    messages: list[dict] = [{"role": "user", "content": "hi"}]
    list(
        chat_agent.run_chat_loop_stream(
            messages,
            tools=[{"name": "e", "description": "", "input_schema": {"type": "object"}}],
            tool_dispatch=lambda n, a: "",
            max_iterations=4,
            force_final_answer=True,
        )
    )

    # No reminder anywhere in the conversation.
    assert all(
        m.get("content") != chat_agent.FINAL_CYCLE_REMINDER for m in messages
    )
    assert messages[-1]["content"] == "done early"


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
def signed_in_client(tmp_db):
    """Create a user, log in, and hand back a FastAPI test client."""
    from fastapi.testclient import TestClient

    from app.auth import users as users_repo
    from app.main import create_app

    users_repo.create(email="t@example.com", password="hunter22", name="t")

    client = TestClient(create_app())
    resp = client.post(
        "/api/auth/login",
        json={"email": "t@example.com", "password": "hunter22"},
    )
    assert resp.status_code == 200, resp.text
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
    assert resp.status_code == 201, resp.text
    return resp.json()["id"]


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
    assert resp.headers["content-type"].startswith("text/event-stream")
    events = _parse_sse(resp.text)
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
    events = _parse_sse(resp.text)
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
    assert "error" in resp.json()


def test_sse_endpoint_rejects_unknown_session(signed_in_client):
    resp = signed_in_client.post(
        "/api/chat/messages",
        json={"session_id": "no-such-session", "content": "hi"},
    )
    assert resp.status_code == 404
    assert "error" in resp.json()
