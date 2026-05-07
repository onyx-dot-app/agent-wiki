"""Single entry point for LLM calls. Provider/model/keys come from llm_settings (DB).

Two surfaces, one implementation:

* ``stream(messages, ...)``  yields normalized events as they arrive from the
  provider — text deltas, completed tool calls, and a final ``done`` event
  carrying ``stop_reason`` + ``usage``. This is the primitive every caller
  should use when streaming-to-the-user matters.
* ``complete(messages, ...)`` drains a stream and returns the historical
  dict shape ({text, tool_calls, stop_reason, usage}). Use it for one-shot
  callers (trigger evaluator, doc-updater) that only need the final result.

Message and tool shapes are unchanged from the prior API:

    messages = [{"role": "system|user|assistant|tool",
                 "content": str | list,
                 "tool_calls": [...] | None,
                 "tool_call_id": str | None}, ...]

    tools    = [{"name": str, "description": str,
                 "input_schema": {...JSON Schema...}}]

Provider-specific translation lives in ``_anthropic_stream`` and
``_openai_stream``. OpenAI uses the **Responses API** (not Chat Completions).
"""
from __future__ import annotations

import json
from functools import lru_cache
from typing import Any, Iterator

from app.llm.settings import get as get_llm_settings

DEFAULT_MAX_TOKENS = 4096

StreamEvent = dict[str, Any]
# Event shapes:
#   {"type": "text_delta",  "text": str}
#   {"type": "tool_call",   "id": str, "name": str, "arguments": dict}
#   {"type": "done",        "stop_reason": str,
#                            "usage": {"input_tokens": int, "output_tokens": int}}


class LLMError(Exception):
    """Provider-agnostic, user-presentable LLM failure.

    `code` is a short, stable string the API layer can use to pick an HTTP
    status (e.g. "not_configured" → 503, "auth" → 502, "rate_limit" → 429).
    `message` is safe to show the user — keep it short, don't include keys,
    secrets, or full SDK stack traces.
    """

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def stream(
    messages: list[dict[str, Any]],
    *,
    model: str | None = None,
    tools: list[dict[str, Any]] | None = None,
    max_tokens: int = DEFAULT_MAX_TOKENS,
) -> Iterator[StreamEvent]:
    """Stream a single LLM completion. Yields normalized event dicts."""
    settings = get_llm_settings()
    if not settings.provider:
        raise LLMError(
            "not_configured",
            "LLM is not configured. An admin needs to set the provider, model, and API key on the admin page.",
        )
    if not settings.model:
        raise LLMError(
            "not_configured",
            f"No model selected for provider '{settings.provider}'. An admin needs to set the model on the admin page.",
        )
    model = model or settings.model
    if settings.provider == "anthropic":
        if not settings.anthropic_api_key:
            raise LLMError(
                "not_configured",
                "Anthropic API key is not set. An admin needs to add it on the admin page.",
            )
        yield from _anthropic_stream(
            messages, model=model, tools=tools, max_tokens=max_tokens, api_key=settings.anthropic_api_key
        )
        return
    if settings.provider == "openai":
        if not settings.openai_api_key:
            raise LLMError(
                "not_configured",
                "OpenAI API key is not set. An admin needs to add it on the admin page.",
            )
        yield from _openai_stream(
            messages, model=model, tools=tools, max_tokens=max_tokens, api_key=settings.openai_api_key
        )
        return
    raise LLMError("not_configured", f"Unknown LLM provider: {settings.provider}")


def complete(
    messages: list[dict[str, Any]],
    *,
    model: str | None = None,
    tools: list[dict[str, Any]] | None = None,
    max_tokens: int = DEFAULT_MAX_TOKENS,
) -> dict[str, Any]:
    """Drain ``stream()`` into the historical dict for non-streaming callers."""
    text_parts: list[str] = []
    tool_calls: list[dict[str, Any]] = []
    stop_reason = ""
    usage = {"input_tokens": 0, "output_tokens": 0}
    for ev in stream(messages, model=model, tools=tools, max_tokens=max_tokens):
        t = ev["type"]
        if t == "text_delta":
            text_parts.append(ev["text"])
        elif t == "tool_call":
            tool_calls.append(
                {"id": ev["id"], "name": ev["name"], "arguments": ev["arguments"]}
            )
        elif t == "done":
            stop_reason = ev["stop_reason"]
            usage = ev["usage"]
    return {
        "text": "".join(text_parts),
        "tool_calls": tool_calls,
        "stop_reason": stop_reason,
        "usage": usage,
    }


# --------------------------------------------------------------------------- #
# Anthropic
# --------------------------------------------------------------------------- #


@lru_cache(maxsize=4)
def _anthropic_client(api_key: str):
    from anthropic import Anthropic

    return Anthropic(api_key=api_key)


def _anthropic_stream(messages, *, model, tools, max_tokens, api_key) -> Iterator[StreamEvent]:
    system, convo = _split_system(messages)
    kwargs: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": [_anthropic_message(m) for m in convo],
    }
    if system is not None:
        # Cache the (usually large, stable) system prompt so multi-turn loops
        # don't repay for it on every call.
        kwargs["system"] = [
            {"type": "text", "text": system, "cache_control": {"type": "ephemeral"}},
        ]
    if tools:
        kwargs["tools"] = [
            {
                "name": t["name"],
                "description": t.get("description", ""),
                "input_schema": t["input_schema"],
            }
            for t in tools
        ]

    client = _anthropic_client(api_key)
    try:
        with client.messages.stream(**kwargs) as s:
            # Per content-block index, accumulate tool-use args (streamed as
            # input_json_delta chunks). Text blocks are emitted as we go.
            tool_blocks: dict[int, dict[str, Any]] = {}
            for event in s:
                etype = getattr(event, "type", None)
                if etype == "content_block_start":
                    block = event.content_block
                    if getattr(block, "type", None) == "tool_use":
                        tool_blocks[event.index] = {
                            "id": block.id,
                            "name": block.name,
                            "buf": "",
                        }
                elif etype == "content_block_delta":
                    delta = event.delta
                    dtype = getattr(delta, "type", None)
                    if dtype == "text_delta":
                        yield {"type": "text_delta", "text": delta.text}
                    elif dtype == "input_json_delta" and event.index in tool_blocks:
                        tool_blocks[event.index]["buf"] += delta.partial_json
                elif etype == "content_block_stop" and event.index in tool_blocks:
                    tb = tool_blocks.pop(event.index)
                    yield {
                        "type": "tool_call",
                        "id": tb["id"],
                        "name": tb["name"],
                        "arguments": _safe_json_loads(tb["buf"]),
                    }
            final = s.get_final_message()
        yield {
            "type": "done",
            "stop_reason": getattr(final, "stop_reason", "") or "",
            "usage": {
                "input_tokens": getattr(final.usage, "input_tokens", 0),
                "output_tokens": getattr(final.usage, "output_tokens", 0),
            },
        }
    except LLMError:
        raise
    except Exception as exc:
        raise _translate_anthropic_error(exc) from exc


def _anthropic_message(m: dict[str, Any]) -> dict[str, Any]:
    """Translate one OpenAI-style message into Anthropic's content-block shape."""
    role = m["role"]
    if role == "tool":
        # OpenAI-style tool result -> Anthropic tool_result block on a user turn.
        return {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": m["tool_call_id"],
                    "content": m["content"] if isinstance(m["content"], str) else json.dumps(m["content"]),
                }
            ],
        }
    if role == "assistant" and m.get("tool_calls"):
        blocks: list[dict[str, Any]] = []
        if m.get("content"):
            blocks.append({"type": "text", "text": m["content"]})
        for tc in m["tool_calls"]:
            blocks.append(
                {
                    "type": "tool_use",
                    "id": tc["id"],
                    "name": tc["name"],
                    "input": tc["arguments"],
                }
            )
        return {"role": "assistant", "content": blocks}

    return {"role": role, "content": m["content"]}


# --------------------------------------------------------------------------- #
# OpenAI (Responses API)
# --------------------------------------------------------------------------- #


@lru_cache(maxsize=4)
def _openai_client(api_key: str):
    from openai import OpenAI

    return OpenAI(api_key=api_key)


def _openai_stream(messages, *, model, tools, max_tokens, api_key) -> Iterator[StreamEvent]:
    instructions, convo = _split_system(messages)

    input_items: list[dict[str, Any]] = []
    for m in convo:
        items = _openai_input_items_for(m)
        input_items.extend(items)

    kwargs: dict[str, Any] = {
        "model": model,
        "input": input_items,
        "max_output_tokens": max_tokens,
        "stream": True,
    }
    if instructions:
        kwargs["instructions"] = instructions
    if tools:
        # Responses API uses a flat function-tool envelope (no nested
        # {"type": "function", "function": {...}} wrapper from chat.completions).
        kwargs["tools"] = [
            {
                "type": "function",
                "name": t["name"],
                "description": t.get("description", ""),
                "parameters": t["input_schema"],
            }
            for t in tools
        ]

    client = _openai_client(api_key)
    try:
        events = client.responses.create(**kwargs)
        # item_id (an internal id) -> {call_id, name, args_buf}.
        # Note: item_id (used in delta events) is distinct from call_id (the
        # tool_call_id we emit and that callers must echo back as
        # function_call_output).
        fc_items: dict[str, dict[str, Any]] = {}
        for event in events:
            etype = getattr(event, "type", None)
            if etype == "response.output_text.delta":
                yield {"type": "text_delta", "text": event.delta}
            elif etype == "response.output_item.added":
                item = event.item
                if getattr(item, "type", None) == "function_call":
                    fc_items[item.id] = {
                        "call_id": item.call_id,
                        "name": item.name,
                        "args_buf": "",
                    }
            elif etype == "response.function_call_arguments.delta":
                rec = fc_items.get(event.item_id)
                if rec is not None:
                    rec["args_buf"] += event.delta
            elif etype == "response.function_call_arguments.done":
                rec = fc_items.pop(event.item_id, None)
                if rec is not None:
                    yield {
                        "type": "tool_call",
                        "id": rec["call_id"],
                        "name": rec["name"],
                        "arguments": _safe_json_loads(rec["args_buf"]),
                    }
            elif etype == "response.completed":
                resp = event.response
                usage = getattr(resp, "usage", None)
                yield {
                    "type": "done",
                    # Responses API has no exact `stop_reason`; status carries
                    # "completed" / "incomplete". Map to the same string key
                    # so callers don't branch on provider.
                    "stop_reason": getattr(resp, "status", "") or "",
                    "usage": {
                        "input_tokens": getattr(usage, "input_tokens", 0) if usage else 0,
                        "output_tokens": getattr(usage, "output_tokens", 0) if usage else 0,
                    },
                }
    except LLMError:
        raise
    except Exception as exc:
        raise _translate_openai_error(exc) from exc


def _openai_input_items_for(m: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the Responses-API input items for one OpenAI-style message.

    A single source message can produce *multiple* items (an assistant turn
    that contains both text and tool calls), so this returns a list.
    """
    role = m["role"]
    if role == "tool":
        content = m["content"] if isinstance(m["content"], str) else json.dumps(m["content"])
        return [
            {
                "type": "function_call_output",
                "call_id": m["tool_call_id"],
                "output": content,
            }
        ]
    if role == "assistant" and m.get("tool_calls"):
        items: list[dict[str, Any]] = []
        if m.get("content"):
            items.append({"role": "assistant", "content": m["content"]})
        for tc in m["tool_calls"]:
            items.append(
                {
                    "type": "function_call",
                    "call_id": tc["id"],
                    "name": tc["name"],
                    "arguments": json.dumps(tc["arguments"]),
                }
            )
        return items
    return [{"role": role, "content": m["content"]}]


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _safe_json_loads(s: str) -> dict[str, Any]:
    if not s:
        return {}
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        return {"_raw": s}


def _translate_anthropic_error(exc: Exception) -> LLMError:
    """Map an Anthropic SDK exception to an LLMError. Import here to keep the
    SDK contained — see CLAUDE.md."""
    import anthropic

    if isinstance(exc, anthropic.AuthenticationError):
        return LLMError("auth", "Anthropic rejected the API key. An admin needs to update it on the admin page.")
    if isinstance(exc, anthropic.PermissionDeniedError):
        return LLMError("auth", "Anthropic denied access for the configured API key.")
    if isinstance(exc, anthropic.RateLimitError):
        return LLMError("rate_limit", "Anthropic rate limit hit. Please retry in a moment.")
    if isinstance(exc, anthropic.APIConnectionError):
        return LLMError("network", "Could not reach Anthropic. Check the backend's network access.")
    if isinstance(exc, anthropic.NotFoundError):
        return LLMError("config", "Anthropic returned 'not found' — usually a bad model name. Check the configured model.")
    if isinstance(exc, anthropic.BadRequestError):
        return LLMError("bad_request", f"Anthropic rejected the request: {exc}")
    if isinstance(exc, anthropic.APIStatusError):
        return LLMError("provider", f"Anthropic error: {exc}")
    return LLMError("unknown", "Unexpected error talking to Anthropic.")


def _translate_openai_error(exc: Exception) -> LLMError:
    import openai

    if isinstance(exc, openai.AuthenticationError):
        return LLMError("auth", "OpenAI rejected the API key. An admin needs to update it on the admin page.")
    if isinstance(exc, openai.PermissionDeniedError):
        return LLMError("auth", "OpenAI denied access for the configured API key.")
    if isinstance(exc, openai.RateLimitError):
        return LLMError("rate_limit", "OpenAI rate limit hit. Please retry in a moment.")
    if isinstance(exc, openai.APIConnectionError):
        return LLMError("network", "Could not reach OpenAI. Check the backend's network access.")
    if isinstance(exc, openai.NotFoundError):
        return LLMError("config", "OpenAI returned 'not found' — usually a bad model name. Check the configured model.")
    if isinstance(exc, openai.BadRequestError):
        return LLMError("bad_request", f"OpenAI rejected the request: {exc}")
    if isinstance(exc, openai.APIStatusError):
        return LLMError("provider", f"OpenAI error: {exc}")
    return LLMError("unknown", "Unexpected error talking to OpenAI.")


def _split_system(messages: list[dict[str, Any]]) -> tuple[str | None, list[dict[str, Any]]]:
    """Pull system messages out of the list (Anthropic + OpenAI Responses both
    take them as a separate arg)."""
    system_parts: list[str] = []
    rest: list[dict[str, Any]] = []
    for m in messages:
        if m["role"] == "system":
            system_parts.append(m["content"])
        else:
            rest.append(m)
    system = "\n\n".join(system_parts) if system_parts else None
    return system, rest
