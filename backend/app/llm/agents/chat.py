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
from contextlib import contextmanager
from typing import Any, Callable, Generator, Iterator

from app.llm import client
from app.llm.agents import skills as skill_registry
from app.llm.agents import tools as tool_registry
from app.llm.prompts import load_prompt
from app.tracing import start_tool_span
from app.wiki import agent_activity

log = logging.getLogger(__name__)


# Surfaced to other users via the agent-activity registry / "Active
# agents" panel when this agent reads or writes a doc.
CHAT_AGENT_NAME = "Wiki AI Assistant"


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
ToolsProvider = Callable[[list["Message"]], list[dict[str, Any]]]
StreamEvent = dict[str, Any]
# Yielded event shapes (superset of client.stream events):
#   {"type": "text_delta",  "text": str}
#   {"type": "tool_call",   "id": str, "name": str, "arguments": dict}
#   {"type": "tool_result", "id": str, "name": str, "content": str}
#   {"type": "done"}                  # final assistant turn produced
#   {"type": "iteration_done"}        # one model turn finished, may loop again

DEFAULT_SYSTEM_PROMPT = "You are a helpful AI assistant"
DEFAULT_MAX_ITERATIONS = 16

FINAL_CYCLE_REMINDER = (
    "<system-reminder> You are on your last cycle, you must not use any more "
    "tools, directly answer the request to the best of your abilities, clarify "
    "any limitations encountered if you are unable to provide a full response. "
    "</system-reminder>"
)


def run_chat_loop_stream(
    messages: list[Message],
    *,
    system_prompt: str = DEFAULT_SYSTEM_PROMPT,
    tools: list[dict[str, Any]] | None = None,
    tools_provider: ToolsProvider | None = None,
    tool_dispatch: ToolDispatch | None = None,
    model: str | None = None,
    provider: str | None = None,
    max_iterations: int = DEFAULT_MAX_ITERATIONS,
    force_final_answer: bool = False,
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

    ``tools_provider`` (if given) takes precedence over ``tools`` and is
    invoked once per iteration with the live ``messages`` list, so the tool
    list can change between turns (used by the chat agent's skills mechanism).

    If ``force_final_answer`` is True and the loop reaches its final allowed
    iteration, a user-role ``<system-reminder>`` message is appended and the
    model is called with no tools — guaranteeing a textual answer instead of
    raising ``RuntimeError`` on iteration exhaustion.
    """
    if (tools or tools_provider) and tool_dispatch is None:
        raise ValueError("tool_dispatch is required when tools are provided")

    _ensure_system_prompt(messages, system_prompt)

    yield from _drive_loop(
        messages,
        tools=tools,
        tools_provider=tools_provider,
        tool_dispatch=tool_dispatch,
        model=model,
        provider=provider,
        max_iterations=max_iterations,
        force_final_answer=force_final_answer,
    )


def _drive_loop(
    messages: list[Message],
    *,
    tools: list[dict[str, Any]] | None,
    tools_provider: ToolsProvider | None,
    tool_dispatch: ToolDispatch | None,
    model: str | None,
    provider: str | None = None,
    max_iterations: int,
    force_final_answer: bool = False,
) -> Iterator[StreamEvent]:
    for i in range(max_iterations):
        text_parts: list[str] = []
        tool_calls: list[dict[str, Any]] = []

        is_final_cycle = force_final_answer and i == max_iterations - 1
        if is_final_cycle:
            messages.append({"role": "user", "content": FINAL_CYCLE_REMINDER})
            turn_tools = None
        else:
            turn_tools = tools_provider(messages) if tools_provider is not None else tools

        for ev in client.stream(messages, model=model, provider=provider, tools=turn_tools):
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
            with start_tool_span(name=call["name"], arguments=call["arguments"]) as tspan:
                try:
                    result = tool_dispatch(call["name"], call["arguments"])
                except Exception as exc:  # surface tool errors back to the model
                    log.exception("tool dispatch failed name=%s id=%s", call["name"], call["id"])
                    result = {"error": str(exc)}
                if tspan is not None:
                    tspan.log(output=result)
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
    tools_provider: ToolsProvider | None = None,
    tool_dispatch: ToolDispatch | None = None,
    model: str | None = None,
    max_iterations: int = DEFAULT_MAX_ITERATIONS,
    force_final_answer: bool = False,
) -> list[Message]:
    """Drain the streaming loop. Returns ``messages`` for caller convenience."""
    for _ in run_chat_loop_stream(
        messages,
        system_prompt=system_prompt,
        tools=tools,
        tools_provider=tools_provider,
        tool_dispatch=tool_dispatch,
        model=model,
        max_iterations=max_iterations,
        force_final_answer=force_final_answer,
    ):
        pass
    return messages


def _chat_tools_for_turn(messages: list[Message]) -> list[dict[str, Any]]:
    """Per-turn tool list for the chat agent: base + comment search + load_skill
    + active skills. ``search_comments`` is wired here (not in
    ``BASE_TOOL_NAMES``) so it stays chat-only — the shared base tools also feed
    the MCP-exposed ``ask_nl_question`` engine, which we don't expand yet."""
    return [
        *skill_registry.base_tool_specs(),
        tool_registry.spec_by_name("search_comments"),
        skill_registry.build_load_skill_spec(),
        *skill_registry.specs_for_active_skills(skill_registry.active_skills(messages)),
    ]


def _chat_dispatch(name: str, args: dict[str, Any]) -> Any:
    """Tool dispatcher for the chat agent — intercepts ``load_skill``."""
    if name == skill_registry.LOAD_SKILL_TOOL_NAME:
        return skill_registry.load_skill_handler(args)
    return tool_registry.dispatch(name, args)


@contextmanager
def chat_agent_scope() -> Generator[None]:
    """Bind ``CHAT_AGENT_NAME`` on ``agent_name_var`` for the enclosed block.

    Must be entered in the caller's own context — *not* inside
    ``run_chat_stream``'s generator body. Starlette's
    ``iterate_in_threadpool`` runs each ``next()`` in a fresh copied
    context, so a Token created in one copy can't be reset from another
    and the ContextVar wouldn't propagate to tool dispatches anyway.
    Wrapping the iteration in this scope sets the var on the async task
    context that ``copy_context()`` propagates into every worker thread.
    """
    token = agent_activity.agent_name_var.set(CHAT_AGENT_NAME)
    try:
        yield
    finally:
        agent_activity.agent_name_var.reset(token)


def run_chat_stream(
    messages: list[Message], *, model: str | None = None, provider: str | None = None
) -> Iterator[StreamEvent]:
    """Streaming chat agent with the standard wiki tool set + chat.system prompt.

    Callers iterating across a thread boundary (e.g. via
    ``iterate_in_threadpool``) must wrap the iteration in
    ``chat_agent_scope()`` so the agent-name ContextVar is bound in their
    own task context.
    """
    return run_chat_loop_stream(
        messages,
        system_prompt=load_prompt("chat.system"),
        tools_provider=_chat_tools_for_turn,
        tool_dispatch=_chat_dispatch,
        model=model,
        provider=provider,
        force_final_answer=True,
    )


def messages_from_history(history: list[dict[str, Any]]) -> list[Message]:
    """Rebuild the agent-format message list from persisted chat rows.

    ``history`` is the output of ``app.chat.sessions.get_messages``: one row
    per persisted user / assistant turn, with assistant rows carrying the
    full streamed event log under ``events``. We replay each assistant
    turn's events back into the same ``{assistant, tool_calls} +
    {role: "tool", tool_call_id, content}`` shape ``_drive_loop`` produces
    live, so the model sees its prior tool calls and tool results when
    continuing the conversation. Without this, the model loses all
    knowledge of what tools it ran in earlier turns and what they returned.
    """
    out: list[Message] = []
    for row in history:
        role = row.get("role")
        content = row.get("content", "")
        events = row.get("events")
        if role == "user" or not events:
            out.append({"role": role, "content": content})
            continue
        out.extend(_replay_assistant_events(events, fallback_text=content))
    return out


def _replay_assistant_events(
    events: list[dict[str, Any]], *, fallback_text: str
) -> list[Message]:
    """Convert one assistant row's saved event log back into messages.

    The live loop alternates ``assistant`` (text + tool_calls) with one
    ``role: "tool"`` per call, then loops. Events arrive as
    ``text_delta`` → ``tool_call`` → ``tool_result`` → ``iteration_done``
    per iteration, with ``done`` ending the final iteration. We flush an
    iteration's accumulated state on either terminator.
    """
    out: list[Message] = []
    text_parts: list[str] = []
    tool_calls: list[dict[str, Any]] = []
    tool_results: dict[str, str] = {}

    def flush() -> None:
        if not text_parts and not tool_calls:
            return
        msg: Message = {"role": "assistant", "content": "".join(text_parts)}
        if tool_calls:
            msg["tool_calls"] = list(tool_calls)
        out.append(msg)
        for call in tool_calls:
            out.append(
                {
                    "role": "tool",
                    "tool_call_id": call["id"],
                    "content": tool_results.get(call["id"], ""),
                }
            )
        text_parts.clear()
        tool_calls.clear()
        tool_results.clear()

    for ev in events:
        t = ev.get("type")
        if t == "text_delta":
            text_parts.append(ev.get("text", ""))
        elif t == "tool_call":
            tool_calls.append(
                {
                    "id": ev["id"],
                    "name": ev["name"],
                    "arguments": ev.get("arguments", {}),
                }
            )
        elif t == "tool_result":
            tool_results[ev["id"]] = ev.get("content", "")
        elif t in ("iteration_done", "done"):
            flush()

    # Flush anything left over (defensive — a clean run ends with `done`).
    flush()

    if not out:
        out.append({"role": "assistant", "content": fallback_text})
    return out


def run_chat(messages: list[Message], *, model: str | None = None) -> list[Message]:
    """Non-streaming wrapper. Mutates and returns ``messages``."""
    with chat_agent_scope():
        return run_chat_loop(
            messages,
            system_prompt=load_prompt("chat.system"),
            tools_provider=_chat_tools_for_turn,
            tool_dispatch=_chat_dispatch,
            model=model,
            force_final_answer=True,
        )


def _ensure_system_prompt(messages: list[Message], system_prompt: str) -> None:
    if any(m["role"] == "system" for m in messages):
        return
    messages.insert(0, {"role": "system", "content": system_prompt})


def _stringify(value: Any) -> str:
    try:
        return json.dumps(value)
    except (TypeError, ValueError):
        return str(value)
