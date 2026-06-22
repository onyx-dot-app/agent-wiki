"""Anthropic provider — Messages API streaming."""

from __future__ import annotations

import json
import logging
from functools import lru_cache
from typing import Any, Iterator, cast

from anthropic import Anthropic

from app.llm.errors import LLMError
from app.llm.providers._common import (
    PREFLIGHT_TIMEOUT_SECONDS,
    run_preflight,
    safe_json_loads,
    split_system,
)
from app.llm.settings import LLMSettings

log = logging.getLogger(__name__)

StreamEvent = dict[str, Any]


@lru_cache(maxsize=4)
def _client(api_key: str) -> Anthropic:
    """Cached Anthropic client. Cache size is small but >1 because tests
    swap keys between cases; production uses a single key."""
    return Anthropic(api_key=api_key)


class AnthropicProvider:
    name = "anthropic"

    def check_configured(self, settings: LLMSettings) -> None:
        if not settings.anthropic_api_key:
            raise LLMError(
                "not_configured",
                "Anthropic API key is not set. An admin needs to add it on the admin page.",
            )

    def test_connection(self, settings: LLMSettings, *, model: str) -> dict[str, Any]:
        """Preflight the saved Anthropic config without touching the cached client."""
        client = Anthropic(
            api_key=settings.anthropic_api_key,
            timeout=PREFLIGHT_TIMEOUT_SECONDS,
            max_retries=0,
        )
        return run_preflight(
            base_url="",
            auth_present=bool(settings.anthropic_api_key),
            model=model,
            listing=lambda: cast(Any, client).models.list(),
            completion=lambda: cast(Any, client.messages).create(
                model=model,
                max_tokens=1,
                messages=[{"role": "user", "content": "ping"}],
            ),
            translate=_translate_error,
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
        system, convo = split_system(messages)
        anthropic_messages = [_message(m) for m in convo]
        # Second-tier cache breakpoints on the conversation (the system
        # breakpoint below caches tools+system). Two ways to place them:
        #
        #  * Explicit: a caller marks specific messages with ``cache: True``
        #    when it knows a prefix will be reread (e.g. the ingest stages tag
        #    the incoming-document message so the candidate batches that follow
        #    read it from cache instead of reprocessing it every batch).
        #  * Automatic: with no explicit marks, a multi-iteration agent loop
        #    re-sends the whole growing history each call, so we breakpoint the
        #    tail and iteration N+1 reads everything through iteration N.
        #
        # Explicit marks win — a caller that placed them doesn't want a stray
        # tail breakpoint on volatile trailing content (e.g. per-batch
        # candidates). Single-message calls with no mark get nothing: no prior
        # turn to reread, so the write would just be wasted.
        explicit = [i for i, m in enumerate(convo) if m.get("cache")]
        if explicit:
            for i in explicit:
                _mark_block_ephemeral(anthropic_messages[i])
        elif len(anthropic_messages) > 1:
            _mark_block_ephemeral(anthropic_messages[-1])
        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "messages": anthropic_messages,
        }
        if system is not None:
            # Cache the (usually large, stable) system prompt so multi-turn
            # loops don't repay for it on every call.
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

        log.info(
            "llm request provider=anthropic model=%s tools=%d max_tokens=%d msgs=%d",
            model,
            len(tools or []),
            max_tokens,
            len(convo),
        )
        client = _client(settings.anthropic_api_key)
        try:
            with client.messages.stream(**kwargs) as s:
                # Per content-block index, accumulate tool-use args (streamed
                # as input_json_delta chunks). Text blocks are emitted as we go.
                tool_blocks: dict[int, dict[str, Any]] = {}
                for raw_event in s:
                    # The SDK's stream is a union of ~14 event types narrowed
                    # by a runtime `.type` string. Pyright can't follow that
                    # discriminator pattern, so we drop into Any for the loop.
                    event = cast(Any, raw_event)
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
                            "arguments": safe_json_loads(tb["buf"]),
                        }
                final = cast(Any, s.get_final_message())
            in_tok = cast(int, getattr(final.usage, "input_tokens", 0))
            out_tok = cast(int, getattr(final.usage, "output_tokens", 0))
            # Anthropic's input_tokens EXCLUDES cached tokens; cache reads/writes
            # are separate. Uncached = fresh input + cache writes (writes are
            # full-price fresh input); cached = cache reads.
            cache_read = cast(int, getattr(final.usage, "cache_read_input_tokens", 0) or 0)
            cache_write = cast(int, getattr(final.usage, "cache_creation_input_tokens", 0) or 0)
            log.info(
                "llm done provider=anthropic model=%s stop=%s tokens=%d/%d cache_read=%d cache_write=%d",
                model,
                getattr(final, "stop_reason", "") or "",
                in_tok,
                out_tok,
                cache_read,
                cache_write,
            )
            yield {
                "type": "done",
                "stop_reason": getattr(final, "stop_reason", "") or "",
                "usage": {
                    "input_tokens": in_tok,
                    "output_tokens": out_tok,
                    "reasoning_tokens": 0,
                    "cached_input_tokens": cache_read,
                    "uncached_input_tokens": in_tok + cache_write,
                },
            }
        except LLMError:
            raise
        except Exception as exc:
            log.exception("llm provider error provider=anthropic model=%s", model)
            raise _translate_error(exc) from exc


def _mark_block_ephemeral(message: dict[str, Any]) -> None:
    """Add a cache breakpoint to the final content block of ``message``.

    Everything rendered up to and including this block becomes a cacheable
    prefix, so a later call sharing that prefix reads it back instead of
    reprocessing it. A string ``content`` is promoted to a single text block
    so the marker has somewhere to live."""
    content = message["content"]
    if isinstance(content, str):
        if not content:
            return  # don't emit an empty text block
        message["content"] = [
            {"type": "text", "text": content, "cache_control": {"type": "ephemeral"}}
        ]
    elif content:
        content[-1] = {**content[-1], "cache_control": {"type": "ephemeral"}}


def _message(m: dict[str, Any]) -> dict[str, Any]:
    """Translate one normalized message into Anthropic's content-block shape."""
    role = m["role"]
    if role == "tool":
        # Normalized tool result -> Anthropic tool_result block on a user turn.
        return {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": m["tool_call_id"],
                    "content": m["content"]
                    if isinstance(m["content"], str)
                    else json.dumps(m["content"]),
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


def _translate_error(exc: Exception) -> LLMError:
    """Map an Anthropic SDK exception to an LLMError. Imports the SDK locally
    so the failure path is the only place we touch the SDK error types."""
    import anthropic

    if isinstance(exc, anthropic.AuthenticationError):
        return LLMError(
            "auth", "Anthropic rejected the API key. An admin needs to update it on the admin page."
        )
    if isinstance(exc, anthropic.PermissionDeniedError):
        return LLMError("auth", "Anthropic denied access for the configured API key.")
    if isinstance(exc, anthropic.RateLimitError):
        return LLMError("rate_limit", "Anthropic rate limit hit. Please retry in a moment.")
    if isinstance(exc, anthropic.APIConnectionError):
        return LLMError("network", "Could not reach Anthropic. Check the backend's network access.")
    if isinstance(exc, anthropic.NotFoundError):
        return LLMError(
            "config",
            "Anthropic returned 'not found' — usually a bad model name. Check the configured model.",
        )
    if isinstance(exc, anthropic.BadRequestError):
        return LLMError("bad_request", f"Anthropic rejected the request: {exc}")
    if isinstance(exc, anthropic.APIStatusError):
        return LLMError("provider", f"Anthropic error: {exc}")
    return LLMError("unknown", "Unexpected error talking to Anthropic.")


PROVIDER = AnthropicProvider()


from app.llm.providers import register  # noqa: E402

register(PROVIDER)  # pyright: ignore[reportUnknownMemberType]
