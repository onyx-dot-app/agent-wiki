"""OpenAI provider — Responses API streaming (NOT Chat Completions)."""

from __future__ import annotations

import json
import logging
from functools import lru_cache
from typing import Any, Iterator, cast

from openai import OpenAI

from app.llm.errors import LLMError
from app.llm.providers._common import (
    PREFLIGHT_TIMEOUT_SECONDS,
    run_preflight,
    safe_json_loads,
    split_system,
)
from app.llm.providers._openai_errors import translate_openai_error
from app.llm.settings import LLMSettings

log = logging.getLogger(__name__)

StreamEvent = dict[str, Any]


@lru_cache(maxsize=4)
def _client(api_key: str) -> OpenAI:
    """Cached OpenAI client. See _client docstring in anthropic.py."""
    return OpenAI(api_key=api_key)


def embed(api_key: str, model: str, inputs: list[str]) -> list[list[float]]:
    """One embedding vector per input, ordered to match ``inputs``.

    The SDK call surface for embeddings lives here (with the rest of the OpenAI
    provider) so no module above ``providers/`` imports the SDK. Reuses the same
    cached ``_client`` as chat, so there's a single client-construction path.

    OpenAI rejects empty strings, so blanks are sent as a space. The API may
    return items out of order; each carries its input position in ``.index``,
    so results are sorted by it before extraction.
    """
    payload = [t if t else " " for t in inputs]
    resp = cast(Any, _client(api_key)).embeddings.create(model=model, input=payload)
    return [list(d.embedding) for d in sorted(resp.data, key=lambda d: d.index)]


class OpenAIProvider:
    name = "openai"

    def check_configured(self, settings: LLMSettings) -> None:
        if not settings.openai_api_key:
            raise LLMError(
                "not_configured",
                "OpenAI API key is not set. An admin needs to add it on the admin page.",
            )

    def test_connection(self, settings: LLMSettings, *, model: str) -> dict[str, Any]:
        """Preflight the saved OpenAI config without touching the cached client."""
        client = OpenAI(
            api_key=settings.openai_api_key,
            timeout=PREFLIGHT_TIMEOUT_SECONDS,
            max_retries=0,
        )
        return run_preflight(
            base_url="",
            auth_present=bool(settings.openai_api_key),
            model=model,
            listing=lambda: cast(Any, client).models.list(),
            completion=lambda: cast(Any, client.responses).create(
                model=model,
                input="ping",
                max_output_tokens=16,
            ),
            translate=lambda exc: translate_openai_error(exc, provider_label="OpenAI"),
        )

    def stream(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str,
        tools: list[dict[str, Any]] | None,
        max_tokens: int,
        settings: LLMSettings,
    ) -> Iterator[StreamEvent]:
        instructions, convo = split_system(messages)

        input_items: list[dict[str, Any]] = []
        for m in convo:
            input_items.extend(_input_items_for(m))

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

        log.info(
            "llm request provider=openai model=%s tools=%d max_tokens=%d items=%d",
            model,
            len(tools or []),
            max_tokens,
            len(input_items),
        )
        client = _client(settings.openai_api_key)
        try:
            events = cast(Any, client.responses.create(**kwargs))
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
                            "arguments": safe_json_loads(rec["args_buf"]),
                        }
                # EVERY terminal status, not just success. A run that hits
                # ``max_output_tokens`` ends with ``response.incomplete``; a server-side failure
                # ends with ``response.failed``. Handling only ``response.completed`` meant those
                # two yielded no terminal event at all, so ``client.complete`` returned the
                # partial text with an empty ``stop_reason`` and zero usage — a truncated answer
                # indistinguishable from a whole one, and no exception raised. That is how
                # entity-type extraction silently lost every large page: the model emitted valid
                # JSON, was cut off mid-list, and the caller saw only "malformed JSON".
                elif etype in ("response.completed", "response.incomplete", "response.failed"):
                    resp = event.response
                    usage = getattr(resp, "usage", None)
                    in_tok = getattr(usage, "input_tokens", 0) if usage else 0
                    out_tok = getattr(usage, "output_tokens", 0) if usage else 0
                    details = getattr(usage, "output_tokens_details", None) if usage else None
                    reason_tok = getattr(details, "reasoning_tokens", 0) if details else 0
                    # OpenAI's input_tokens INCLUDES cached tokens; the cached
                    # portion is reported under input_tokens_details. Uncached =
                    # input_tokens - cached.
                    in_details = getattr(usage, "input_tokens_details", None) if usage else None
                    cached_tok = getattr(in_details, "cached_tokens", 0) if in_details else 0
                    status = getattr(resp, "status", "") or ""
                    # Why it stopped, when the API says: "max_output_tokens" or "content_filter".
                    # Worth logging because the fix differs — a bigger cap versus a changed prompt.
                    incomplete = getattr(resp, "incomplete_details", None)
                    reason = getattr(incomplete, "reason", "") or ""
                    log_at = log.info if status == "completed" else log.warning
                    log_at(
                        "llm done provider=openai model=%s status=%s%s tokens=%d/%d cached=%d",
                        model,
                        status or "(none)",
                        f" reason={reason}" if reason else "",
                        in_tok,
                        out_tok,
                        cached_tok or 0,
                    )
                    yield {
                        "type": "done",
                        # Responses API has no exact `stop_reason`; status carries
                        # "completed" / "incomplete". Map to the same string key
                        # so callers don't branch on provider.
                        "stop_reason": status,
                        "usage": {
                            "input_tokens": in_tok,
                            "output_tokens": out_tok,
                            "reasoning_tokens": reason_tok or 0,
                            "cached_input_tokens": cached_tok or 0,
                            "uncached_input_tokens": max(0, (in_tok or 0) - (cached_tok or 0)),
                        },
                    }
        except LLMError:
            raise
        except Exception as exc:
            log.exception("llm provider error provider=openai model=%s", model)
            raise translate_openai_error(exc, provider_label="OpenAI") from exc


def _input_items_for(m: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the Responses-API input items for one normalized message.

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


PROVIDER = OpenAIProvider()


from app.llm.providers import register  # noqa: E402

register(PROVIDER)  # pyright: ignore[reportUnknownMemberType]
