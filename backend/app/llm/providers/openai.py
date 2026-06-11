"""OpenAI provider — Responses API streaming (NOT Chat Completions)."""
from __future__ import annotations

import json
import logging
from functools import lru_cache
from typing import Any, Iterator, cast

from openai import OpenAI

from app.llm.errors import LLMError
from app.llm.providers._common import safe_json_loads, split_system
from app.llm.providers._openai_errors import translate_openai_error
from app.llm.settings import LLMSettings

log = logging.getLogger(__name__)

StreamEvent = dict[str, Any]


@lru_cache(maxsize=4)
def _client(api_key: str) -> OpenAI:
    """Cached OpenAI client. See _client docstring in anthropic.py."""
    return OpenAI(api_key=api_key)


class OpenAIProvider:
    name = "openai"

    def check_configured(self, settings: LLMSettings) -> None:
        if not settings.openai_api_key:
            raise LLMError(
                "not_configured",
                "OpenAI API key is not set. An admin needs to add it on the admin page.",
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
            model, len(tools or []), max_tokens, len(input_items),
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
                elif etype == "response.completed":
                    resp = event.response
                    usage = getattr(resp, "usage", None)
                    in_tok = getattr(usage, "input_tokens", 0) if usage else 0
                    out_tok = getattr(usage, "output_tokens", 0) if usage else 0
                    details = getattr(usage, "output_tokens_details", None) if usage else None
                    reason_tok = getattr(details, "reasoning_tokens", 0) if details else 0
                    status = getattr(resp, "status", "") or ""
                    log.info(
                        "llm done provider=openai model=%s status=%s tokens=%d/%d",
                        model, status, in_tok, out_tok,
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
