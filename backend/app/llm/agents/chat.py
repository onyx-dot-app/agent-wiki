"""Chat agent backing the in-app ChatUI.

`run_chat_loop_stream` is the core primitive: a streaming, multi-iteration
tool-use loop. It yields normalized events (text deltas, completed tool
calls, tool results, end-of-turn) AND mutates `messages` in place so callers
keep the same {role, content, tool_calls, tool_call_id} state they had with
the non-streaming API.

`run_chat_stream` and `run_chat` are convenience wrappers that preconfigure
the standard wiki tool set (see `app.llm.agents.tools`).
"""
from __future__ import annotations

import json
from typing import Any, Callable, Iterator

from app.llm import client
from app.llm.agents.tools import CHAT_TOOL_SPECS, dispatch_chat_tool
from app.llm.prompts import load_prompt

Message = dict[str, Any]
ToolDispatch = Callable[[str, dict[str, Any]], Any]
StreamEvent = dict[str, Any]
# Yielded event shapes (superset of client.stream events):
#   {"type": "text_delta",  "text": str}
#   {"type": "tool_call",   "id": str, "name": str, "arguments": dict}
#   {"type": "tool_result", "id": str, "name": str, "content": str}
#   {"type": "done"}                  # final assistant turn produced
#   {"type": "iteration_done"}        # one model turn finished, may loop again

DEFAULT_MAX_ITERATIONS = 8


def run_chat_loop_stream(
    messages: list[Message],
    *,
    tools: list[dict[str, Any]] | None = None,
    tool_dispatch: ToolDispatch | None = None,
    model: str | None = None,
    max_iterations: int = DEFAULT_MAX_ITERATIONS,
) -> Iterator[StreamEvent]:
    """Stream the chat agent until a final assistant turn (no tool calls).

    Behavior contract (mirrors the prior non-streaming loop):

    * `messages` is mutated in place: each model turn appends one
      ``{"role": "assistant", "content": ..., "tool_calls": ...}`` entry,
      followed by one ``{"role": "tool", ...}`` entry per tool call.
    * The function yields events in the order they happen, so callers can
      forward to the browser as-is.
    * The loop stops when the model returns a turn without any tool calls.
      ``max_iterations`` is a hard cap to prevent runaway loops.
    """
    if tools and tool_dispatch is None:
        raise ValueError("tool_dispatch is required when tools are provided")

    _ensure_system_prompt(messages)

    for _ in range(max_iterations):
        text_parts: list[str] = []
        tool_calls: list[dict[str, Any]] = []

        for ev in client.stream(messages, model=model, tools=tools):
            t = ev["type"]
            if t == "text_delta":
                text_parts.append(ev["text"])
                yield ev
            elif t == "tool_call":
                tool_calls.append(
                    {"id": ev["id"], "name": ev["name"], "arguments": ev["arguments"]}
                )
                yield ev
            elif t == "done":
                # Don't forward — we synthesize iteration_done / done below
                # so the SSE consumer sees a single terminal event per turn.
                pass

        assistant_turn: Message = {"role": "assistant", "content": "".join(text_parts)}
        if tool_calls:
            assistant_turn["tool_calls"] = tool_calls
        messages.append(assistant_turn)

        if not tool_calls:
            yield {"type": "done"}
            return

        # tool_dispatch is non-None here (guarded above; tools implies dispatch).
        assert tool_dispatch is not None
        for call in tool_calls:
            try:
                result = tool_dispatch(call["name"], call["arguments"])
            except Exception as exc:  # surface tool errors back to the model
                result = {"error": str(exc)}
            content_str = result if isinstance(result, str) else _stringify(result)
            messages.append(
                {"role": "tool", "tool_call_id": call["id"], "content": content_str}
            )
            yield {
                "type": "tool_result",
                "id": call["id"],
                "name": call["name"],
                "content": content_str,
            }

        yield {"type": "iteration_done"}

    raise RuntimeError(
        f"chat loop did not terminate within {max_iterations} iterations"
    )


def run_chat_loop(
    messages: list[Message],
    *,
    tools: list[dict[str, Any]] | None = None,
    tool_dispatch: ToolDispatch | None = None,
    model: str | None = None,
    max_iterations: int = DEFAULT_MAX_ITERATIONS,
) -> list[Message]:
    """Drain the streaming loop. Returns ``messages`` for caller convenience."""
    for _ in run_chat_loop_stream(
        messages,
        tools=tools,
        tool_dispatch=tool_dispatch,
        model=model,
        max_iterations=max_iterations,
    ):
        pass
    return messages


def run_chat_stream(
    messages: list[Message], *, model: str | None = None
) -> Iterator[StreamEvent]:
    """Streaming chat agent with the standard wiki tool set."""
    yield from run_chat_loop_stream(
        messages,
        tools=CHAT_TOOL_SPECS,
        tool_dispatch=dispatch_chat_tool,
        model=model,
    )


def run_chat(messages: list[Message], *, model: str | None = None) -> list[Message]:
    """Non-streaming wrapper. Mutates and returns ``messages``."""
    return run_chat_loop(
        messages,
        tools=CHAT_TOOL_SPECS,
        tool_dispatch=dispatch_chat_tool,
        model=model,
    )


def _ensure_system_prompt(messages: list[Message]) -> None:
    if any(m["role"] == "system" for m in messages):
        return
    messages.insert(0, {"role": "system", "content": load_prompt("chat.system")})


def _stringify(value: Any) -> str:
    try:
        return json.dumps(value)
    except (TypeError, ValueError):
        return str(value)


def run_chat_turn(user_id: str, conversation_id: str, message: str) -> dict:
    """Persistence-aware wrapper used by the HTTP layer.

    Loads prior turns for `conversation_id`, appends the new user `message`,
    runs `run_chat_loop`, persists the new turns, and returns a payload for
    the frontend.

    Not yet implemented — the DB schema for conversations isn't in place.
    """
    raise NotImplementedError
