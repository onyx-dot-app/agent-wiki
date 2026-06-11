"""Custom provider — OpenAI-compatible gateways via Chat Completions."""

from __future__ import annotations

import json
import logging
from functools import lru_cache
from typing import Any, Iterator, cast

import openai
from openai import OpenAI

from app.llm.errors import LLMError
from app.llm.providers._common import safe_json_loads, stringify_tool_result
from app.llm.providers._openai_errors import translate_openai_error
from app.llm.settings import LLMSettings

log = logging.getLogger(__name__)

StreamEvent = dict[str, Any]

_KEYLESS_API_KEY = "EMPTY"  # vLLM/LM Studio allow keyless mode; the SDK still needs a value.


@lru_cache(maxsize=4)
def _client(api_key: str, base_url: str) -> OpenAI:
    """Cached OpenAI client for compat gateways."""
    return OpenAI(api_key=api_key or _KEYLESS_API_KEY, base_url=base_url)


def _create_stream(client: OpenAI, kwargs: dict[str, Any]) -> Any:
    """Retry once for gateways that reject stream usage metadata."""
    completions = cast(Any, client.chat).completions
    try:
        return completions.create(**kwargs, stream_options={"include_usage": True})
    except openai.BadRequestError as exc:
        msg = str(exc).lower()
        if "stream_options" not in msg and "include_usage" not in msg:
            raise
    return completions.create(**kwargs)


class CustomProvider:
    name = "custom"

    def check_configured(self, settings: LLMSettings) -> None:
        if not settings.custom_base_url:
            raise LLMError(
                "not_configured",
                "Custom provider base URL is not set. An admin needs to add it on the admin page.",
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
        cc_messages = [_cc_message(m) for m in messages]

        kwargs: dict[str, Any] = {
            "model": model,
            "messages": cc_messages,
            "max_tokens": max_tokens,
            "stream": True,
        }
        if tools:
            kwargs["tools"] = [_cc_tool(t) for t in tools]

        log.info(
            "llm request provider=custom model=%s tools=%d max_tokens=%d messages=%d",
            model,
            len(tools or []),
            max_tokens,
            len(cc_messages),
        )
        client = _client(settings.custom_api_key, settings.custom_base_url)
        try:
            stop_reason = ""
            usage: Any = None
            tool_chunks: dict[int, dict[str, Any]] = {}
            chunks = _create_stream(client, kwargs)
            for raw_chunk in chunks:
                chunk = raw_chunk
                if not chunk.choices:
                    chunk_usage = getattr(chunk, "usage", None)
                    if chunk_usage is not None:
                        usage = chunk_usage
                    continue

                choice = chunk.choices[0]
                delta = getattr(choice, "delta", None)
                if delta is not None:
                    if delta.content:
                        yield {"type": "text_delta", "text": delta.content}
                    raw_tool_calls = getattr(delta, "tool_calls", None)
                    if raw_tool_calls:
                        for raw_tc in raw_tool_calls:
                            tc = raw_tc
                            index = getattr(tc, "index", None)
                            if index is None:
                                continue
                            rec = tool_chunks.setdefault(
                                index,
                                {"id": None, "name": None, "args_buf": ""},
                            )
                            tc_id = getattr(tc, "id", None)
                            if rec["id"] is None and tc_id is not None:
                                rec["id"] = tc_id
                            fn = getattr(tc, "function", None)
                            if fn is not None:
                                fn_name = getattr(fn, "name", None)
                                if rec["name"] is None and fn_name is not None:
                                    rec["name"] = fn_name
                                fn_args = getattr(fn, "arguments", None)
                                if fn_args:
                                    rec["args_buf"] += fn_args

                finish_reason = getattr(choice, "finish_reason", None)
                if finish_reason is not None:
                    stop_reason = cast(str, finish_reason)

                chunk_usage = getattr(chunk, "usage", None)
                if chunk_usage is not None:
                    usage = chunk_usage

            for index in sorted(tool_chunks.keys()):
                rec = tool_chunks[index]
                yield {
                    "type": "tool_call",
                    "id": rec["id"] or f"call_{index}",
                    "name": rec["name"] or "",
                    "arguments": safe_json_loads(rec["args_buf"]),
                }

            in_tok = int(getattr(usage, "prompt_tokens", 0) or 0)
            out_tok = int(getattr(usage, "completion_tokens", 0) or 0)
            details = getattr(usage, "completion_tokens_details", None)
            reason_tok = int(getattr(details, "reasoning_tokens", 0) or 0)
            log.info(
                "llm done provider=custom model=%s stop=%s tokens=%d/%d",
                model,
                stop_reason,
                in_tok,
                out_tok,
            )
            yield {
                "type": "done",
                "stop_reason": stop_reason,
                "usage": {
                    "input_tokens": in_tok,
                    "output_tokens": out_tok,
                    "reasoning_tokens": reason_tok,
                },
            }
        except LLMError:
            raise
        except Exception as exc:
            log.exception("llm provider error provider=custom model=%s", model)
            raise translate_openai_error(exc, provider_label="Custom provider") from exc


def _cc_message(m: dict[str, Any]) -> dict[str, Any]:
    role = m["role"]
    if role == "assistant" and m.get("tool_calls"):
        content = m.get("content") or None
        return {
            "role": "assistant",
            "content": content,
            "tool_calls": [
                {
                    "id": tc["id"],
                    "type": "function",
                    "function": {
                        "name": tc["name"],
                        "arguments": json.dumps(tc["arguments"]),
                    },
                }
                for tc in m["tool_calls"]
            ],
        }
    if role == "tool":
        return {
            "role": "tool",
            "tool_call_id": m["tool_call_id"],
            "content": stringify_tool_result(m["content"]),
        }
    return {"role": role, "content": m["content"]}


def _cc_tool(tool: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": tool["name"],
            "description": tool.get("description", ""),
            "parameters": tool["input_schema"],
        },
    }


PROVIDER = CustomProvider()


from app.llm.providers import register  # noqa: E402

register(PROVIDER)  # pyright: ignore[reportUnknownMemberType]
