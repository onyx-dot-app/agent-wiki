"""Bedrock Converse provider — request translation + stream decoding at the seam.

Pure-translation coverage (no AWS) plus provider-level stream tests that patch
the boto3 client factory, asserting the normalized text/tool_call/done events
and botocore-error -> LLMError mapping.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.llm.errors import LLMError
from app.llm.providers import bedrock
from app.llm.providers._converse import build_converse_request, iter_stream_events
from app.llm.settings import LLMSettings


def test_build_request_splits_system_and_wraps_text() -> None:
    req = build_converse_request(
        [
            {"role": "system", "content": "be terse"},
            {"role": "user", "content": "hi"},
        ],
        model="m",
        tools=None,
        max_tokens=7,
    )
    assert req["modelId"] == "m"
    assert req["system"] == [{"text": "be terse"}]
    assert req["inferenceConfig"] == {"maxTokens": 7}
    assert req["messages"] == [{"role": "user", "content": [{"text": "hi"}]}]
    assert "toolConfig" not in req


def test_build_request_tools_and_tool_roundtrip() -> None:
    req = build_converse_request(
        [
            {"role": "user", "content": "go"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"id": "t1", "name": "search", "arguments": {"q": "x"}}],
            },
            {"role": "tool", "tool_call_id": "t1", "content": "result text"},
        ],
        model="m",
        tools=[{"name": "search", "description": "find", "input_schema": {"type": "object"}}],
        max_tokens=10,
    )
    assert req["toolConfig"]["tools"] == [
        {
            "toolSpec": {
                "name": "search",
                "inputSchema": {"json": {"type": "object"}},
                "description": "find",
            }
        }
    ]
    assert req["messages"][1] == {
        "role": "assistant",
        "content": [{"toolUse": {"toolUseId": "t1", "name": "search", "input": {"q": "x"}}}],
    }
    assert req["messages"][2] == {
        "role": "user",
        "content": [{"toolResult": {"toolUseId": "t1", "content": [{"text": "result text"}]}}],
    }


def test_iter_stream_events_text_and_done() -> None:
    events = [
        {"messageStart": {"role": "assistant"}},
        {"contentBlockDelta": {"contentBlockIndex": 0, "delta": {"text": "Hel"}}},
        {"contentBlockDelta": {"contentBlockIndex": 0, "delta": {"text": "lo"}}},
        {"contentBlockStop": {"contentBlockIndex": 0}},
        {"messageStop": {"stopReason": "end_turn"}},
        {"metadata": {"usage": {"inputTokens": 5, "outputTokens": 2, "totalTokens": 7}}},
    ]
    out = list(iter_stream_events(events))
    assert out[0] == {"type": "text_delta", "text": "Hel"}
    assert out[1] == {"type": "text_delta", "text": "lo"}
    done = out[-1]
    assert done["type"] == "done"
    assert done["stop_reason"] == "end_turn"
    assert done["usage"]["input_tokens"] == 5
    assert done["usage"]["output_tokens"] == 2
    assert done["usage"]["uncached_input_tokens"] == 5


def test_iter_stream_events_tool_call_accumulates_input() -> None:
    events = [
        {
            "contentBlockStart": {
                "contentBlockIndex": 0,
                "start": {"toolUse": {"toolUseId": "t9", "name": "lookup"}},
            }
        },
        {"contentBlockDelta": {"contentBlockIndex": 0, "delta": {"toolUse": {"input": '{"q":'}}}},
        {"contentBlockDelta": {"contentBlockIndex": 0, "delta": {"toolUse": {"input": '"hi"}'}}}},
        {"contentBlockStop": {"contentBlockIndex": 0}},
        {"messageStop": {"stopReason": "tool_use"}},
        {"metadata": {"usage": {"inputTokens": 1, "outputTokens": 1, "totalTokens": 2}}},
    ]
    out = list(iter_stream_events(events))
    tool_calls = [e for e in out if e["type"] == "tool_call"]
    assert tool_calls == [
        {"type": "tool_call", "id": "t9", "name": "lookup", "arguments": {"q": "hi"}}
    ]
    assert out[-1]["stop_reason"] == "tool_use"


def test_iter_stream_events_cache_tokens() -> None:
    events = [
        {"messageStop": {"stopReason": "end_turn"}},
        {
            "metadata": {
                "usage": {
                    "inputTokens": 10,
                    "outputTokens": 3,
                    "totalTokens": 13,
                    "cacheReadInputTokens": 100,
                    "cacheWriteInputTokens": 20,
                }
            }
        },
    ]
    done = list(iter_stream_events(events))[-1]
    assert done["usage"]["cached_input_tokens"] == 100
    assert done["usage"]["uncached_input_tokens"] == 30  # inputTokens + cacheWrite


class _FakeClient:
    def __init__(
        self, events: list[dict[str, Any]] | None = None, error: Exception | None = None
    ) -> None:
        self._events = events or []
        self._error = error

    def converse_stream(self, **kwargs: Any) -> dict[str, Any]:
        if self._error is not None:
            raise self._error
        return {"stream": iter(self._events)}


def _settings() -> LLMSettings:
    return LLMSettings(provider="bedrock", model="m", bedrock_aws_region="us-gov-west-1")


def test_provider_stream_yields_normalized_events(monkeypatch: pytest.MonkeyPatch) -> None:
    events = [
        {"contentBlockDelta": {"contentBlockIndex": 0, "delta": {"text": "ok"}}},
        {"messageStop": {"stopReason": "end_turn"}},
        {"metadata": {"usage": {"inputTokens": 1, "outputTokens": 1, "totalTokens": 2}}},
    ]
    monkeypatch.setattr(bedrock, "_client", lambda *a, **k: _FakeClient(events))
    out = list(
        bedrock.PROVIDER.stream(
            [{"role": "user", "content": "hi"}],
            model="m",
            tools=None,
            max_tokens=4,
            settings=_settings(),
        )
    )
    assert {"type": "text_delta", "text": "ok"} in out
    assert out[-1]["type"] == "done"


def test_provider_stream_translates_client_error(monkeypatch: pytest.MonkeyPatch) -> None:
    from botocore.exceptions import ClientError

    err = ClientError({"Error": {"Code": "ThrottlingException", "Message": "slow"}}, "Converse")
    monkeypatch.setattr(bedrock, "_client", lambda *a, **k: _FakeClient(error=err))
    with pytest.raises(LLMError) as excinfo:
        list(
            bedrock.PROVIDER.stream(
                [{"role": "user", "content": "hi"}],
                model="m",
                tools=None,
                max_tokens=4,
                settings=_settings(),
            )
        )
    assert excinfo.value.code == "rate_limit"


def test_check_configured_requires_region() -> None:
    with pytest.raises(LLMError) as excinfo:
        bedrock.PROVIDER.check_configured(LLMSettings(provider="bedrock", model="m"))
    assert excinfo.value.code == "not_configured"
