"""Single entry point for LLM calls. Dispatches to the configured provider.

Provider implementations live behind the plural seam at
``app/llm/providers/``. This module is the public surface every other
module in the codebase uses — keep it small.

Two surfaces, one implementation:

* ``stream(messages, ...)`` yields normalized events as they arrive from the
  provider — text deltas, completed tool calls, and a final ``done`` event
  carrying ``stop_reason`` + ``usage``. This is the primitive every caller
  should use when streaming-to-the-user matters.
* ``complete(messages, ...)`` drains a stream and returns the historical
  dict shape ({text, tool_calls, stop_reason, usage}). Use it for one-shot
  callers (trigger evaluator, doc-updater) that only need the final result.

Normalized message shape:

    messages = [{"role": "system|user|assistant|tool",
                 "content": str | list,
                 "tool_calls": [...] | None,
                 "tool_call_id": str | None}, ...]

    tools    = [{"name": str, "description": str,
                 "input_schema": {...JSON Schema...}}]

Stream event shapes:

    {"type": "text_delta",  "text": str}
    {"type": "tool_call",   "id": str, "name": str, "arguments": dict}
    {"type": "done",        "stop_reason": str,
                             "usage": {"input_tokens": int,
                                       "output_tokens": int,
                                       "reasoning_tokens": int}}
"""
from __future__ import annotations

import json
import logging
from typing import Any, Iterator

from pydantic import BaseModel, Field

from app.llm import providers
from app.llm.errors import LLMError
from app.llm.redact import scrub_secrets
from app.llm.settings import get as get_llm_settings
from app.tracing import start_llm_span, to_openai_message_shape

log = logging.getLogger(__name__)

DEFAULT_MAX_TOKENS = 4096

StreamEvent = dict[str, Any]


class ToolCall(BaseModel):
    """Normalized tool-call shape emitted by every provider."""

    id: str
    name: str
    arguments: dict[str, Any]


class Usage(BaseModel):
    """Token counts reported by the provider for a single completion."""

    input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0


class CompletionResult(BaseModel):
    """Drained result of a single LLM completion. Provider-agnostic."""

    text: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)
    stop_reason: str = ""
    usage: Usage = Field(default_factory=Usage)


def _debug_dump(label: str, obj: Any) -> None:
    """Pretty-print ``obj`` to the log at DEBUG, untruncated (secrets scrubbed).

    Skips serialization entirely when DEBUG isn't enabled — no cost on the
    hot path. Use for full LLM payloads (messages, tools, responses).
    """
    if not log.isEnabledFor(logging.DEBUG):
        return
    try:
        rendered = json.dumps(obj, indent=2, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        rendered = repr(obj)
    log.debug("%s\n%s", label, scrub_secrets(rendered))


def stream(
    messages: list[dict[str, Any]],
    *,
    model: str | None = None,
    provider: str | None = None,
    tools: list[dict[str, Any]] | None = None,
    max_tokens: int = DEFAULT_MAX_TOKENS,
) -> Iterator[StreamEvent]:
    """Stream a single LLM completion. Yields normalized event dicts.

    Logs the full request (messages + tools) and the full response (text +
    tool calls + usage including reasoning tokens) at DEBUG, once per call.
    Provider modules don't dump payloads themselves — this is the single
    spot the whole exchange is captured.
    """
    _debug_dump("llm request messages", messages)
    if tools:
        _debug_dump("llm request tools", tools)
    settings = get_llm_settings()
    chosen_provider_name = provider or settings.provider
    if not chosen_provider_name:
        raise LLMError(
            "not_configured",
            "LLM is not configured. An admin needs to set the provider, model, and API key on the admin page.",
        )
    chosen_provider = providers.get(chosen_provider_name)
    if chosen_provider is None:
        raise LLMError("not_configured", f"Unknown LLM provider: {chosen_provider_name}")
    chosen_model = model or settings.model
    if not chosen_model:
        raise LLMError(
            "not_configured",
            f"No model selected for provider '{chosen_provider_name}'. An admin needs to set the model on the admin page.",
        )
    chosen_provider.check_configured(settings)
    text_parts: list[str] = []
    tool_calls: list[dict[str, Any]] = []
    stop_reason = ""
    usage: dict[str, int] = {"input_tokens": 0, "output_tokens": 0, "reasoning_tokens": 0}
    with start_llm_span(
        provider=chosen_provider_name,
        model=chosen_model,
        messages=messages,
        tools=tools,
        max_tokens=max_tokens,
    ) as span:
        for ev in chosen_provider.stream(
            messages,
            model=chosen_model,
            tools=tools,
            max_tokens=max_tokens,
            settings=settings,
        ):
            t = ev.get("type")
            if t == "text_delta":
                text_parts.append(ev["text"])
            elif t == "tool_call":
                tool_calls.append(
                    {"id": ev["id"], "name": ev["name"], "arguments": ev["arguments"]}
                )
            elif t == "done":
                stop_reason = ev.get("stop_reason", "") or ""
                usage = ev.get("usage") or usage
                # Log the output on the ``done`` event, BEFORE the yield —
                # if the caller abandons iteration after this (or never
                # advances past the final yield), the span still carries
                # the full output. Doing it post-loop is fragile because
                # a GeneratorExit raised at the yield unwinds the ``with``
                # without ever hitting the post-loop branch.
                if span is not None:
                    span.log(
                        output=to_openai_message_shape(
                            [_build_assistant_message("".join(text_parts), tool_calls)]
                        ),
                        metrics=_usage_to_metrics(usage),
                        metadata={"stop_reason": stop_reason},
                    )
            yield ev
    _debug_dump(
        "llm response",
        {
            "text": "".join(text_parts),
            "tool_calls": tool_calls,
            "stop_reason": stop_reason,
            "usage": usage,
        },
    )


def _build_assistant_message(
    text: str, tool_calls: list[dict[str, Any]]
) -> dict[str, Any]:
    """Build the normalized assistant message for this turn.

    Uses our internal shape (``tool_calls: [{id, name, arguments}]``);
    ``to_openai_message_shape`` does the OpenAI-shape translation at the
    Braintrust seam so the UI renders tool calls correctly.
    """
    msg: dict[str, Any] = {"role": "assistant", "content": text}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    return msg


def _usage_to_metrics(usage: dict[str, int]) -> dict[str, int]:
    """Translate our normalized usage dict to the Braintrust ``metrics``
    keys the UI renders (prompt_tokens / completion_tokens / tokens)."""
    prompt_tokens = int(usage.get("input_tokens", 0))
    completion_tokens = int(usage.get("output_tokens", 0))
    reasoning_tokens = int(usage.get("reasoning_tokens", 0))
    metrics: dict[str, int] = {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "tokens": prompt_tokens + completion_tokens,
    }
    if reasoning_tokens:
        metrics["reasoning_tokens"] = reasoning_tokens
    return metrics


def complete(
    messages: list[dict[str, Any]],
    *,
    model: str | None = None,
    tools: list[dict[str, Any]] | None = None,
    max_tokens: int = DEFAULT_MAX_TOKENS,
) -> CompletionResult:
    """Drain ``stream()`` into a ``CompletionResult`` for non-streaming callers.

    Request/response DEBUG logging happens inside ``stream()`` — don't
    re-log here.
    """
    text_parts: list[str] = []
    tool_calls: list[ToolCall] = []
    stop_reason = ""
    usage = Usage()
    for ev in stream(messages, model=model, tools=tools, max_tokens=max_tokens):
        t = ev["type"]
        if t == "text_delta":
            text_parts.append(ev["text"])
        elif t == "tool_call":
            tool_calls.append(
                ToolCall(id=ev["id"], name=ev["name"], arguments=ev["arguments"])
            )
        elif t == "done":
            stop_reason = ev["stop_reason"]
            usage = Usage(**ev["usage"])
    return CompletionResult(
        text="".join(text_parts),
        tool_calls=tool_calls,
        stop_reason=stop_reason,
        usage=usage,
    )


__all__ = [
    "DEFAULT_MAX_TOKENS",
    "CompletionResult",
    "StreamEvent",
    "ToolCall",
    "Usage",
    "complete",
    "stream",
]
