"""Generic chat loop + the wiki-tooled wrapper backing the in-app ChatUI.

`run_chat_loop_stream` is the core primitive: a streaming, multi-iteration
tool-use loop. It is generic — pass any `system_prompt`, any `tools` list,
and a `tool_dispatch(name, args)` callable, and it yields normalized events
(text deltas, completed tool calls, tool results, end-of-turn) AND mutates
`messages` in place so callers keep the same {role, content, tool_calls,
tool_call_id} state they had with the non-streaming API.

`run_chat_stream` and `run_chat` are convenience wrappers that preconfigure
the standard wiki tool set (see `app.llm.agents.tools`) plus the
`chat.system` prompt.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Callable, Iterator

from app.llm import client
from app.llm.agents import tools as tool_registry
from app.llm.agents._session import seen_doc_paths
from app.llm.prompts import load_prompt

log = logging.getLogger(__name__)


def _debug_dump(label: str, obj: Any) -> None:
    """Pretty-print ``obj`` to the log at DEBUG, untruncated."""
    if not log.isEnabledFor(logging.DEBUG):
        return
    try:
        rendered = json.dumps(obj, indent=2, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        rendered = repr(obj)
    log.debug("%s\n%s", label, rendered)

Message = dict[str, Any]
ToolDispatch = Callable[[str, dict[str, Any]], Any]
StreamEvent = dict[str, Any]
# Yielded event shapes (superset of client.stream events):
#   {"type": "text_delta",  "text": str}
#   {"type": "tool_call",   "id": str, "name": str, "arguments": dict}
#   {"type": "tool_result", "id": str, "name": str, "content": str}
#   {"type": "done"}                  # final assistant turn produced
#   {"type": "iteration_done"}        # one model turn finished, may loop again

DEFAULT_SYSTEM_PROMPT = "You are a helpful AI assistant"
DEFAULT_MAX_ITERATIONS = 8


def run_chat_loop_stream(
    messages: list[Message],
    *,
    system_prompt: str = DEFAULT_SYSTEM_PROMPT,
    tools: list[dict[str, Any]] | None = None,
    tool_dispatch: ToolDispatch | None = None,
    model: str | None = None,
    max_iterations: int = DEFAULT_MAX_ITERATIONS,
) -> Iterator[StreamEvent]:
    """Stream a tool-using chat loop until a final assistant turn (no tool calls).

    Behavior contract (mirrors the prior non-streaming loop):

    * `messages` is mutated in place: each model turn appends one
      ``{"role": "assistant", "content": ..., "tool_calls": ...}`` entry,
      followed by one ``{"role": "tool", ...}`` entry per tool call.
    * If `messages` does not already contain a ``system`` entry,
      `system_prompt` is inserted at index 0.
    * The function yields events in the order they happen, so callers can
      forward to the browser as-is.
    * The loop stops when the model returns a turn without any tool calls.
      ``max_iterations`` is a hard cap to prevent runaway loops.
    """
    if tools and tool_dispatch is None:
        raise ValueError("tool_dispatch is required when tools are provided")

    _ensure_system_prompt(messages, system_prompt)

    # Track wiki paths the model has read this turn so write tools can
    # enforce read-before-write. Reset on exit so callers outside a loop
    # (tests, direct invocation) see the default (None) and skip the check.
    seen_token = seen_doc_paths.set(set())
    try:
        yield from _drive_loop(
            messages,
            tools=tools,
            tool_dispatch=tool_dispatch,
            model=model,
            max_iterations=max_iterations,
        )
    finally:
        seen_doc_paths.reset(seen_token)


def _drive_loop(
    messages: list[Message],
    *,
    tools: list[dict[str, Any]] | None,
    tool_dispatch: ToolDispatch | None,
    model: str | None,
    max_iterations: int,
) -> Iterator[StreamEvent]:
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
        _debug_dump("chat assistant turn", assistant_turn)

        if not tool_calls:
            yield {"type": "done"}
            return

        # tool_dispatch is non-None here (guarded above; tools implies dispatch).
        assert tool_dispatch is not None
        for call in tool_calls:
            _debug_dump("chat tool call", call)
            try:
                result = tool_dispatch(call["name"], call["arguments"])
            except Exception as exc:  # surface tool errors back to the model
                log.exception("tool dispatch failed name=%s id=%s", call["name"], call["id"])
                result = {"error": str(exc)}
            _record_seen_paths(call["name"], result)
            content_str = result if isinstance(result, str) else _stringify(result)
            messages.append(
                {"role": "tool", "tool_call_id": call["id"], "content": content_str}
            )
            _debug_dump(
                f"chat tool result name={call['name']} id={call['id']}",
                content_str,
            )
            yield {
                "type": "tool_result",
                "id": call["id"],
                "name": call["name"],
                "content": content_str,
            }

        yield {"type": "iteration_done"}

    log.warning("chat loop hit iteration limit (%d)", max_iterations)
    raise RuntimeError(
        f"chat loop did not terminate within {max_iterations} iterations"
    )


def run_chat_loop(
    messages: list[Message],
    *,
    system_prompt: str = DEFAULT_SYSTEM_PROMPT,
    tools: list[dict[str, Any]] | None = None,
    tool_dispatch: ToolDispatch | None = None,
    model: str | None = None,
    max_iterations: int = DEFAULT_MAX_ITERATIONS,
) -> list[Message]:
    """Drain the streaming loop. Returns ``messages`` for caller convenience."""
    for _ in run_chat_loop_stream(
        messages,
        system_prompt=system_prompt,
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
    """Streaming chat agent with the standard wiki tool set + chat.system prompt."""
    yield from run_chat_loop_stream(
        messages,
        system_prompt=load_prompt("chat.system"),
        tools=tool_registry.TOOL_SPECS,
        tool_dispatch=tool_registry.dispatch,
        model=model,
    )


def run_chat(messages: list[Message], *, model: str | None = None) -> list[Message]:
    """Non-streaming wrapper. Mutates and returns ``messages``."""
    return run_chat_loop(
        messages,
        system_prompt=load_prompt("chat.system"),
        tools=tool_registry.TOOL_SPECS,
        tool_dispatch=tool_registry.dispatch,
        model=model,
    )


def _ensure_system_prompt(messages: list[Message], system_prompt: str) -> None:
    if any(m["role"] == "system" for m in messages):
        return
    messages.insert(0, {"role": "system", "content": system_prompt})


def _record_seen_paths(tool_name: str, result: Any) -> None:
    """Track which wiki docs the model has actually read this turn.

    Only ``read_page`` counts — ``search_wiki`` returns ~64-token snippets,
    which is not enough context to safely edit a doc. The doc-edit tools
    consult ``seen_doc_paths`` to enforce read-before-write.
    """
    if tool_name != "read_page":
        return
    seen = seen_doc_paths.get()
    if seen is None or not isinstance(result, dict):
        return
    path = result.get("path")
    if isinstance(path, str) and path:
        seen.add(path)


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
