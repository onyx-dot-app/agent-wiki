"""Helpers shared across provider implementations.

Kept private (``_common``) — these are *not* part of the public LLM
interface; they're translation utilities that more than one provider
needs. If something here grows beyond translation glue, give it a
proper home.
"""
from __future__ import annotations

import json
import logging
from typing import Any

log = logging.getLogger("app.llm.providers")


def safe_json_loads(s: str) -> dict[str, Any]:
    """Parse JSON tool-call arguments, never raising.

    Models occasionally emit malformed JSON when truncated. We surface the
    raw string under ``_raw`` so the chat loop can still report what came
    back rather than crashing the request.
    """
    if not s:
        return {}
    try:
        parsed = json.loads(s)
    except json.JSONDecodeError:
        return {"_raw": s}
    if not isinstance(parsed, dict):
        return {"_raw": s}
    return parsed


def split_system(messages: list[dict[str, Any]]) -> tuple[str | None, list[dict[str, Any]]]:
    """Pull system messages out of the list.

    Anthropic, OpenAI Responses, and Gemini all take system text as a
    separate argument (``system`` / ``instructions`` /
    ``system_instruction``). Concatenating multiple system messages with a
    blank-line separator preserves agent-side composition (e.g. a base
    persona + a per-tool reminder).
    """
    system_parts: list[str] = []
    rest: list[dict[str, Any]] = []
    for m in messages:
        if m["role"] == "system":
            system_parts.append(m["content"])
        else:
            rest.append(m)
    system = "\n\n".join(system_parts) if system_parts else None
    return system, rest


def tool_call_id_to_name(messages: list[dict[str, Any]]) -> dict[str, str]:
    """Walk prior assistant turns to map ``tool_call_id -> tool_name``.

    Some providers (Gemini) require the function name on tool-result
    turns. Our normalized message shape only carries ``tool_call_id`` on
    the result, so we recover the name by scanning earlier assistant
    turns where it was emitted.
    """
    mapping: dict[str, str] = {}
    for m in messages:
        if m.get("role") != "assistant":
            continue
        for tc in m.get("tool_calls") or []:
            tc_id = tc.get("id")
            tc_name = tc.get("name")
            if isinstance(tc_id, str) and isinstance(tc_name, str):
                mapping[tc_id] = tc_name
    return mapping


def stringify_tool_result(content: Any) -> str:
    """Tool result -> string. Providers all take strings; structured payloads
    are JSON-encoded so the model sees stable formatting."""
    if isinstance(content, str):
        return content
    try:
        return json.dumps(content)
    except (TypeError, ValueError):
        return str(content)


def debug_dump(label: str, obj: Any) -> None:
    """Pretty-print ``obj`` to the provider log at DEBUG, untruncated.

    Skips serialization entirely when DEBUG isn't enabled — no cost on the
    hot path. Use for full LLM payloads (request kwargs, raw chunks).
    """
    if not log.isEnabledFor(logging.DEBUG):
        return
    try:
        rendered = json.dumps(obj, indent=2, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        rendered = repr(obj)
    log.debug("%s\n%s", label, rendered)
