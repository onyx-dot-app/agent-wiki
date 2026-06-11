"""Gemini provider — google-genai streaming with function calling.

Uses the ``google-genai`` SDK (the unified Gemini client). The wire format
differs from Anthropic/OpenAI in two notable ways:

* Roles are ``user`` / ``model`` (not ``user`` / ``assistant``).
* Tool result turns require the **function name**, not just an id. Our
  normalized message shape only carries ``tool_call_id`` on tool results,
  so we resolve the name by walking earlier assistant turns
  (see ``tool_call_id_to_name``).
"""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import Any, Iterator, cast

from google import genai

from app.llm.errors import LLMError
from app.llm.providers._common import (
    PREFLIGHT_TIMEOUT_SECONDS,
    run_preflight,
    split_system,
    stringify_tool_result,
    tool_call_id_to_name,
)
from app.llm.settings import LLMSettings

log = logging.getLogger(__name__)

StreamEvent = dict[str, Any]


@lru_cache(maxsize=4)
def _client(api_key: str) -> genai.Client:
    """Cached Gemini client. See _client docstring in anthropic.py."""
    return genai.Client(api_key=api_key)


class GeminiProvider:
    name = "gemini"

    def check_configured(self, settings: LLMSettings) -> None:
        if not settings.gemini_api_key:
            raise LLMError(
                "not_configured",
                "Gemini API key is not set. An admin needs to add it on the admin page.",
            )

    def test_connection(self, settings: LLMSettings, *, model: str) -> dict[str, Any]:
        """Preflight the saved Gemini config without touching the cached client."""
        client = genai.Client(api_key=settings.gemini_api_key)
        return run_preflight(
            base_url="",
            auth_present=bool(settings.gemini_api_key),
            model=model,
            listing=lambda: cast(Any, client.models).list(),
            completion=lambda: cast(Any, client.models).generate_content(
                model=model,
                contents=cast(Any, "ping"),
                config=cast(
                    Any,
                    {
                        "max_output_tokens": 1,
                        "http_options": {"timeout": int(PREFLIGHT_TIMEOUT_SECONDS * 1000)},
                    },
                ),
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
        id_to_name = tool_call_id_to_name(convo)
        contents = [_content_for(m, id_to_name) for m in convo]

        config: dict[str, Any] = {"max_output_tokens": max_tokens}
        if system is not None:
            config["system_instruction"] = system
        if tools:
            # One Tool entry containing all function declarations is the
            # canonical Gemini shape — splitting one-per-Tool also works
            # but matches what the SDK examples use.
            config["tools"] = [
                {
                    "function_declarations": [
                        {
                            "name": t["name"],
                            "description": t.get("description", ""),
                            "parameters": _sanitize_schema(t["input_schema"]),
                        }
                        for t in tools
                    ]
                }
            ]

        log.info(
            "llm request provider=gemini model=%s tools=%d max_tokens=%d msgs=%d",
            model,
            len(tools or []),
            max_tokens,
            len(convo),
        )
        client = _client(settings.gemini_api_key)
        try:
            stream = client.models.generate_content_stream(  # pyright: ignore[reportUnknownMemberType]
                model=model, contents=cast(Any, contents), config=cast(Any, config)
            )
            stop_reason = ""
            usage: dict[str, int] = {"input_tokens": 0, "output_tokens": 0}
            tool_call_idx = 0
            for raw_chunk in stream:
                chunk = cast(Any, raw_chunk)
                cands: list[Any] = list(getattr(chunk, "candidates", None) or [])
                for cand in cands:
                    finish = getattr(cand, "finish_reason", None)
                    if finish:
                        # Gemini's enum string ("STOP", "MAX_TOKENS", "TOOL_CALLS"...).
                        stop_reason = str(finish).split(".")[-1].lower()
                    content = getattr(cand, "content", None)
                    parts: list[Any] = list(getattr(content, "parts", None) or [])
                    for part in parts:
                        text = getattr(part, "text", None)
                        if text:
                            yield {"type": "text_delta", "text": text}
                        fc = getattr(part, "function_call", None)
                        if fc is not None:
                            # Newer SDKs put an id on the function_call; older
                            # ones don't. Synthesize a deterministic one so the
                            # caller can echo it back as tool_call_id.
                            fc_id = getattr(fc, "id", None) or f"gemini_tc_{tool_call_idx}"
                            tool_call_idx += 1
                            yield {
                                "type": "tool_call",
                                "id": fc_id,
                                "name": fc.name,
                                "arguments": dict(fc.args or {}),
                            }
                meta = getattr(chunk, "usage_metadata", None)
                if meta is not None:
                    usage = {
                        "input_tokens": getattr(meta, "prompt_token_count", 0) or 0,
                        "output_tokens": getattr(meta, "candidates_token_count", 0) or 0,
                        "reasoning_tokens": getattr(meta, "thoughts_token_count", 0) or 0,
                    }
            log.info(
                "llm done provider=gemini model=%s stop=%s tokens=%d/%d",
                model,
                stop_reason,
                usage["input_tokens"],
                usage["output_tokens"],
            )
            yield {"type": "done", "stop_reason": stop_reason, "usage": usage}
        except LLMError:
            raise
        except Exception as exc:
            log.exception("llm provider error provider=gemini model=%s", model)
            raise _translate_error(exc) from exc


def _content_for(m: dict[str, Any], id_to_name: dict[str, str]) -> dict[str, Any]:
    """Translate one normalized message to a Gemini ``Content`` dict."""
    role = m["role"]
    if role == "tool":
        # Gemini wants the function name on tool results, and the response
        # body must be a dict. String results are wrapped under "result".
        name = id_to_name.get(m.get("tool_call_id") or "", "")
        content = m.get("content")
        response_obj: dict[str, Any]
        if isinstance(content, dict):
            response_obj = cast(dict[str, Any], content)
        else:
            response_obj = {"result": stringify_tool_result(content)}
        return {
            "role": "user",
            "parts": [{"function_response": {"name": name, "response": response_obj}}],
        }
    if role == "assistant" and m.get("tool_calls"):
        parts: list[dict[str, Any]] = []
        if m.get("content"):
            parts.append({"text": m["content"]})
        for tc in m["tool_calls"]:
            parts.append({"function_call": {"name": tc["name"], "args": tc["arguments"]}})
        return {"role": "model", "parts": parts}
    if role == "assistant":
        return {"role": "model", "parts": [{"text": m["content"] or ""}]}
    return {"role": "user", "parts": [{"text": m["content"]}]}


def _sanitize_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Drop JSON Schema keywords Gemini's function-declaration validator rejects.

    Gemini accepts a JSON-Schema-ish subset. Common offenders are
    ``additionalProperties``, ``$schema``, and ``$id``; the OpenAPI-style
    properties (``type``, ``description``, ``enum``, ``properties``,
    ``items``, ``required``) flow through cleanly.
    """
    drop = {"additionalProperties", "$schema", "$id", "$ref", "definitions"}
    out: dict[str, Any] = {}
    for k, v in schema.items():
        if k in drop:
            continue
        if k == "properties" and isinstance(v, dict):
            v_dict: dict[str, Any] = cast(dict[str, Any], v)
            out[k] = {pk: _sanitize_schema(pv) for pk, pv in v_dict.items()}
        elif k == "items":
            out[k] = _sanitize_schema(cast(dict[str, Any], v)) if isinstance(v, dict) else v
        else:
            out[k] = v
    return out


def _translate_error(exc: Exception) -> LLMError:
    # google-genai's exception hierarchy is small; we duck-type on attributes
    # rather than importing every error subclass.
    code = getattr(exc, "code", None) or getattr(exc, "status_code", None)
    msg = str(exc)
    if code in (401, 403) or "API_KEY_INVALID" in msg or "PermissionDenied" in msg:
        return LLMError(
            "auth", "Gemini rejected the API key. An admin needs to update it on the admin page."
        )
    if code == 429 or "RESOURCE_EXHAUSTED" in msg or "rate" in msg.lower():
        return LLMError("rate_limit", "Gemini rate limit hit. Please retry in a moment.")
    if code == 404 or "NotFound" in msg:
        return LLMError(
            "config",
            "Gemini returned 'not found' — usually a bad model name. Check the configured model.",
        )
    if code in (400, 422) or "InvalidArgument" in msg:
        return LLMError("bad_request", f"Gemini rejected the request: {exc}")
    if "ConnectionError" in type(exc).__name__ or "Timeout" in type(exc).__name__:
        return LLMError("network", "Could not reach Gemini. Check the backend's network access.")
    return LLMError("provider", f"Gemini error: {exc}")


PROVIDER = GeminiProvider()


from app.llm.providers import register  # noqa: E402

register(PROVIDER)  # pyright: ignore[reportUnknownMemberType]
