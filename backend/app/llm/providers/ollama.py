"""Ollama provider — local-first models via the ``ollama`` HTTP client.

Differences from cloud providers:

* No API key. ``settings.ollama_base_url`` (e.g. ``http://localhost:11434``)
  is the only credential; empty string means "use the SDK default", which
  is localhost.
* Tool calls have no native id. Ollama returns
  ``message.tool_calls = [{function: {name, arguments}}]`` — we synthesize
  stable per-response ids so the chat loop's ``tool_call_id`` round-trips.
  Going back to the model, Ollama just looks at ``role == "tool"``
  messages in order, so we don't need to echo the synthesized id.
* Tool-call arguments come back as a dict already (no JSON-string
  reassembly).
"""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import Any, Iterator, cast

from ollama import Client

from app.llm.errors import LLMError
from app.llm.providers._common import (
    PREFLIGHT_TIMEOUT_SECONDS,
    run_preflight,
    stringify_tool_result,
)
from app.llm.settings import LLMSettings

log = logging.getLogger(__name__)

StreamEvent = dict[str, Any]


@lru_cache(maxsize=4)
def _client(host: str) -> Client:
    """Cached Ollama client. Empty host means "SDK default" (localhost)."""
    return Client(host=host) if host else Client()


class OllamaProvider:
    name = "ollama"

    def check_configured(self, settings: LLMSettings) -> None:
        # Ollama doesn't require an API key; an empty base URL falls back
        # to the SDK default (localhost). Nothing to validate here — model
        # presence is checked upstream in client.stream.
        return

    def test_connection(self, settings: LLMSettings, *, model: str) -> dict[str, Any]:
        """Preflight the saved Ollama config without touching the cached client."""
        client = (
            Client(host=settings.ollama_base_url, timeout=PREFLIGHT_TIMEOUT_SECONDS)
            if settings.ollama_base_url
            else Client(timeout=PREFLIGHT_TIMEOUT_SECONDS)
        )
        return run_preflight(
            base_url=settings.ollama_base_url,
            auth_present=False,
            model=model,
            listing=lambda: cast(Any, client).list(),
            completion=lambda: cast(Any, client).chat(
                model=model,
                messages=[{"role": "user", "content": "ping"}],
                options={"num_predict": 1},
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
        ollama_messages = [_message(m) for m in messages]

        kwargs: dict[str, Any] = {
            "model": model,
            "messages": ollama_messages,
            "stream": True,
            # Ollama's ``num_predict`` is the max-tokens analog. Per-call
            # options live in ``options`` (the Modelfile parameter set).
            "options": {"num_predict": max_tokens},
        }
        if tools:
            kwargs["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t["name"],
                        "description": t.get("description", ""),
                        "parameters": t["input_schema"],
                    },
                }
                for t in tools
            ]

        log.info(
            "llm request provider=ollama model=%s tools=%d max_tokens=%d msgs=%d",
            model,
            len(tools or []),
            max_tokens,
            len(ollama_messages),
        )
        client = _client(settings.ollama_base_url)
        try:
            stop_reason = ""
            usage: dict[str, int] = {"input_tokens": 0, "output_tokens": 0}
            tool_call_idx = 0
            chat_call = cast(Any, client.chat)
            for raw_chunk in chat_call(**kwargs):
                chunk: Any = raw_chunk
                # ChatResponse is a pydantic model with .message.content,
                # .message.tool_calls, .done, .done_reason, .prompt_eval_count,
                # .eval_count.
                msg = getattr(chunk, "message", None)
                text = getattr(msg, "content", None) if msg else None
                if text:
                    yield {"type": "text_delta", "text": text}
                tcs = getattr(msg, "tool_calls", None) if msg else None
                if tcs:
                    for tc in tcs:
                        fn = getattr(tc, "function", None)
                        if fn is None:
                            continue
                        name = getattr(fn, "name", "")
                        raw_args: Any = getattr(fn, "arguments", None) or {}
                        args: dict[str, Any]
                        if isinstance(raw_args, dict):
                            args = cast(dict[str, Any], raw_args)
                        else:
                            args = {"_raw": str(raw_args)}
                        yield {
                            "type": "tool_call",
                            "id": f"ollama_tc_{tool_call_idx}",
                            "name": name,
                            "arguments": dict(args),
                        }
                        tool_call_idx += 1
                if getattr(chunk, "done", False):
                    stop_reason = getattr(chunk, "done_reason", "") or ""
                    in_tok = getattr(chunk, "prompt_eval_count", 0) or 0
                    out_tok = getattr(chunk, "eval_count", 0) or 0
                    usage = {
                        "input_tokens": in_tok,
                        "output_tokens": out_tok,
                        "reasoning_tokens": 0,
                        # Ollama has no prompt cache: all input is uncached.
                        "cached_input_tokens": 0,
                        "uncached_input_tokens": in_tok,
                    }
            log.info(
                "llm done provider=ollama model=%s stop=%s tokens=%d/%d",
                model,
                stop_reason,
                usage["input_tokens"],
                usage["output_tokens"],
            )
            yield {"type": "done", "stop_reason": stop_reason, "usage": usage}
        except LLMError:
            raise
        except Exception as exc:
            log.exception("llm provider error provider=ollama model=%s", model)
            raise _translate_error(exc) from exc


def _message(m: dict[str, Any]) -> dict[str, Any]:
    """Translate one normalized message to an Ollama chat message dict."""
    role = m["role"]
    if role == "tool":
        # Ollama pairs tool results with the prior assistant tool_calls by
        # order in the message list — no id field is required.
        return {"role": "tool", "content": stringify_tool_result(m.get("content"))}
    if role == "assistant" and m.get("tool_calls"):
        return {
            "role": "assistant",
            "content": m.get("content") or "",
            "tool_calls": [
                {
                    "function": {
                        "name": tc["name"],
                        "arguments": tc["arguments"],
                    }
                }
                for tc in m["tool_calls"]
            ],
        }
    return {"role": role, "content": m["content"] or ""}


def _translate_error(exc: Exception) -> LLMError:
    # The ollama client raises ResponseError with .status_code; ConnectError
    # is a httpx subtype. Duck-type to keep the SDK out of the import path.
    cls_name = type(exc).__name__
    msg = str(exc)
    status = getattr(exc, "status_code", None)
    if cls_name in ("ConnectError", "ConnectTimeout") or "Connection" in msg:
        return LLMError(
            "network",
            "Could not reach Ollama. Check the configured base URL and that the server is running.",
        )
    if status == 404 or "model" in msg.lower() and "not found" in msg.lower():
        return LLMError(
            "config",
            "Ollama doesn't have that model pulled. Run `ollama pull <model>` on the server.",
        )
    if status in (400, 422):
        return LLMError("bad_request", f"Ollama rejected the request: {exc}")
    return LLMError("provider", f"Ollama error: {exc}")


PROVIDER = OllamaProvider()


from app.llm.providers import register  # noqa: E402

register(PROVIDER)  # pyright: ignore[reportUnknownMemberType]
