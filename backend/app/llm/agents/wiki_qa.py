"""One-shot wiki Q&A agent backing the ``ask_nl_question`` tool.

Reuses ``run_chat_loop`` with a narrowed, read-only toolset (the base tools:
``search_wiki``, ``search_comments``, ``read_page`` — no writes). Returns a
structured answer with the paths the agent grounded on.

The MCP layer is what ultimately exposes ``ask_nl_question`` to external
clients, but this module is MCP-agnostic — it's just a normal in-process
agent. Tests patch ``app.llm.client.complete`` (transitively via
``run_chat_loop``).
"""
from __future__ import annotations

import json
import logging
from typing import Any, cast

from app.llm.prompts import load_prompt
from app.tracing import trace_flow

log = logging.getLogger(__name__)

_MAX_ITERATIONS = 10


def run(query: str, *, model: str | None = None) -> dict[str, Any]:
    """Answer ``query`` against the wiki. Returns ``{answer, sources}``.

    ``sources`` is a list of ``{path}`` for every doc the agent actually
    called ``read_page`` on — built from the loop's mutated message log,
    not parsed from the LLM's prose.
    """
    # Lazy imports avoid a cycle: chat.py imports the tool registry at
    # module load, which loads ask_nl_question.py, which imports this
    # module. Deferring the chat / registry imports until call time keeps
    # the import graph acyclic.
    from app.llm.agents import chat as chat_agent  # noqa: PLC0415
    from app.llm.agents import skills as skill_registry  # noqa: PLC0415
    from app.llm.agents import tools as tool_registry  # noqa: PLC0415

    system_prompt = load_prompt("wiki_qa.system")
    # Read-only sub-agent: base toolset only, no `load_skill` (cannot escalate
    # to writes / shell / web).
    tools = skill_registry.base_tool_specs()
    messages: list[dict[str, Any]] = [{"role": "user", "content": query}]
    with trace_flow("agent.wiki_qa", query=query):
        chat_agent.run_chat_loop(
            messages,
            system_prompt=system_prompt,
            tools=tools,
            tool_dispatch=tool_registry.dispatch,
            model=model,
            max_iterations=_MAX_ITERATIONS,
            force_final_answer=True,
        )
    answer = _final_assistant_text(messages)
    sources = _collect_sources(messages)
    return {"answer": answer, "sources": sources}


def _final_assistant_text(messages: list[dict[str, Any]]) -> str:
    for msg in reversed(messages):
        if msg.get("role") != "assistant":
            continue
        content = msg.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            blocks = cast(list[Any], content)
            parts: list[str] = [
                cast(dict[str, Any], blk).get("text", "")
                for blk in blocks
                if isinstance(blk, dict) and cast(dict[str, Any], blk).get("type") == "text"
            ]
            joined = "".join(parts).strip()
            if joined:
                return joined
    return ""


def _collect_sources(messages: list[dict[str, Any]]) -> list[dict[str, str]]:
    """Collect every wiki path the agent fetched via ``read_page``.

    The chat loop appends a ``{"role": "tool", ...}`` message after each
    tool call. We pair tool-result messages with the preceding
    assistant's tool_calls to know which tool produced each result, then
    extract ``path`` from successful ``read_page`` results.
    """
    seen: list[dict[str, str]] = []
    pending_calls: dict[str, str] = {}  # call_id -> tool name
    for msg in messages:
        role = msg.get("role")
        if role == "assistant":
            tool_calls: list[Any] = msg.get("tool_calls") or []
            for call in tool_calls:
                if isinstance(call, dict):
                    call_dict = cast(dict[str, Any], call)
                    pending_calls[call_dict.get("id", "")] = call_dict.get("name", "")
            continue
        if role != "tool":
            continue
        call_id = msg.get("tool_call_id", "")
        if pending_calls.get(call_id) != "read_page":
            continue
        path = _extract_path(msg.get("content"))
        if path and not any(s["path"] == path for s in seen):
            seen.append({"path": path})
    return seen


def _extract_path(content: Any) -> str | None:
    """``read_page`` returns ``{path, title, body}``; the loop stringifies
    via ``json.dumps``. Pull ``path`` back out without re-parsing JSON if
    we can avoid it."""
    if not isinstance(content, str):
        return None
    # Cheap path extraction — avoids loading json for the common shape.
    try:
        parsed: Any = json.loads(content)
    except (TypeError, ValueError):
        return None
    if isinstance(parsed, dict):
        parsed_dict = cast(dict[str, Any], parsed)
        path = parsed_dict.get("path")
        if isinstance(path, str) and path:
            return path
    return None
