"""Tests for the custom Chat Completions provider."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any, cast

import httpx
import openai
import pytest

from app.llm.errors import LLMError
from app.llm.providers import custom as custom_provider
from app.llm.settings import LLMSettings


def _settings(**overrides: Any) -> LLMSettings:
    base: dict[str, Any] = {
        "provider": "custom",
        "model": "custom-model",
        "anthropic_api_key": "",
        "openai_api_key": "",
        "gemini_api_key": "",
        "ollama_base_url": "",
        "custom_api_key": "",
        "custom_base_url": "http://gateway.example/v1",
        "custom_display_name": "Gateway",
        "provider_models": {},
        "ingest_selector_model": "",
    }
    base.update(overrides)
    return LLMSettings(**base)


class _FakeClient:
    def __init__(self, responses: list[Any]):
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if not self._responses:
            return iter(())
        result = self._responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return iter(result)


def _chunk(
    *,
    content: str | None = None,
    tool_calls: list[Any] | None = None,
    finish_reason: str | None = None,
    usage: Any = None,
    choices: list[Any] | None = None,
) -> Any:
    if choices is None:
        choices = [
            SimpleNamespace(
                delta=SimpleNamespace(content=content, tool_calls=tool_calls),
                finish_reason=finish_reason,
            )
        ]
    return SimpleNamespace(choices=choices, usage=usage)


def _tool_delta(
    *,
    index: int,
    id: str | None = None,
    name: str | None = None,
    arguments: str | None = None,
) -> Any:
    return SimpleNamespace(
        index=index,
        id=id,
        function=SimpleNamespace(name=name, arguments=arguments),
    )


def _bad_request(message: str) -> openai.BadRequestError:
    request = httpx.Request("POST", "https://example.test/v1/chat/completions")
    response = httpx.Response(status_code=400, request=request)
    return openai.BadRequestError(message, response=response, body={"message": message})


def _collect(events: Any) -> list[dict[str, Any]]:
    return list(events)


def test_check_configured_raises_when_base_url_missing() -> None:
    provider = custom_provider.CustomProvider()

    with pytest.raises(LLMError) as excinfo:
        provider.check_configured(_settings(custom_base_url=""))

    assert excinfo.value.code == "not_configured"
    assert "admin page" in excinfo.value.message


def test_check_configured_allows_missing_api_key() -> None:
    provider = custom_provider.CustomProvider()

    assert provider.check_configured(_settings(custom_api_key="")) is None


def test_stream_emits_text_deltas_in_order(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(
        [
            [
                _chunk(content="Hel"),
                _chunk(content="lo"),
                _chunk(finish_reason="stop"),
            ]
        ]
    )
    monkeypatch.setattr(custom_provider, "_client", lambda api_key, base_url: fake)
    provider = custom_provider.CustomProvider()

    events = _collect(
        provider.stream(
            [{"role": "user", "content": "Hi"}],
            model="custom-model",
            tools=None,
            max_tokens=32,
            settings=_settings(),
        )
    )

    assert [event for event in events if event["type"] == "text_delta"] == [
        {"type": "text_delta", "text": "Hel"},
        {"type": "text_delta", "text": "lo"},
    ]


def test_stream_reassembles_tool_call_fragments_even_when_finish_reason_is_stop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakeClient(
        [
            [
                _chunk(
                    tool_calls=[
                        _tool_delta(index=0, id="call_abc", name="lookup", arguments='{"city"')
                    ]
                ),
                _chunk(tool_calls=[_tool_delta(index=0, arguments=':"SF"}')]),
                _chunk(finish_reason="stop"),
            ]
        ]
    )
    monkeypatch.setattr(custom_provider, "_client", lambda api_key, base_url: fake)
    provider = custom_provider.CustomProvider()

    events = _collect(
        provider.stream(
            [{"role": "user", "content": "Use tool"}],
            model="custom-model",
            tools=None,
            max_tokens=32,
            settings=_settings(),
        )
    )

    assert events[0] == {
        "type": "tool_call",
        "id": "call_abc",
        "name": "lookup",
        "arguments": {"city": "SF"},
    }
    assert events[1]["type"] == "done"
    assert events[1]["stop_reason"] == "stop"


def test_stream_flushes_parallel_tool_calls_in_index_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakeClient(
        [
            [
                _chunk(
                    tool_calls=[_tool_delta(index=1, id="call_b", name="second", arguments='{"b":')]
                ),
                _chunk(
                    tool_calls=[_tool_delta(index=0, id="call_a", name="first", arguments='{"a":')]
                ),
                _chunk(tool_calls=[_tool_delta(index=1, arguments="2}")]),
                _chunk(tool_calls=[_tool_delta(index=0, arguments="1}")]),
                _chunk(finish_reason="stop"),
            ]
        ]
    )
    monkeypatch.setattr(custom_provider, "_client", lambda api_key, base_url: fake)
    provider = custom_provider.CustomProvider()

    events = _collect(
        provider.stream(
            [{"role": "user", "content": "Use tools"}],
            model="custom-model",
            tools=None,
            max_tokens=32,
            settings=_settings(),
        )
    )

    tool_events = [event for event in events if event["type"] == "tool_call"]
    assert tool_events == [
        {"type": "tool_call", "id": "call_a", "name": "first", "arguments": {"a": 1}},
        {"type": "tool_call", "id": "call_b", "name": "second", "arguments": {"b": 2}},
    ]


def test_stream_handles_empty_tool_arguments(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient(
        [
            [
                _chunk(
                    tool_calls=[_tool_delta(index=0, id="call_empty", name="noop", arguments="")]
                ),
                _chunk(finish_reason="stop"),
            ]
        ]
    )
    monkeypatch.setattr(custom_provider, "_client", lambda api_key, base_url: fake)
    provider = custom_provider.CustomProvider()

    events = _collect(
        provider.stream(
            [{"role": "user", "content": "No args"}],
            model="custom-model",
            tools=None,
            max_tokens=32,
            settings=_settings(),
        )
    )

    assert events[0]["arguments"] == {}


def test_stream_uses_usage_only_final_chunk(monkeypatch: pytest.MonkeyPatch) -> None:
    usage = SimpleNamespace(
        prompt_tokens=11,
        completion_tokens=7,
        completion_tokens_details=SimpleNamespace(reasoning_tokens=3),
    )
    fake = _FakeClient([[_chunk(content="Hi"), _chunk(choices=[], usage=usage)]])
    monkeypatch.setattr(custom_provider, "_client", lambda api_key, base_url: fake)
    provider = custom_provider.CustomProvider()

    events = _collect(
        provider.stream(
            [{"role": "user", "content": "hello"}],
            model="custom-model",
            tools=None,
            max_tokens=32,
            settings=_settings(),
        )
    )

    assert events[-1] == {
        "type": "done",
        "stop_reason": "",
        "usage": {"input_tokens": 11, "output_tokens": 7, "reasoning_tokens": 3},
    }


def test_stream_emits_exactly_one_done_with_verbatim_stop_reason_and_usage_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    usage = SimpleNamespace(prompt_tokens=4, completion_tokens=6)
    fake = _FakeClient([[_chunk(content="ok"), _chunk(finish_reason="gateway_stop", usage=usage)]])
    monkeypatch.setattr(custom_provider, "_client", lambda api_key, base_url: fake)
    provider = custom_provider.CustomProvider()

    events = _collect(
        provider.stream(
            [{"role": "user", "content": "hello"}],
            model="custom-model",
            tools=None,
            max_tokens=32,
            settings=_settings(),
        )
    )

    done_events = [event for event in events if event["type"] == "done"]
    assert len(done_events) == 1
    assert done_events[0]["stop_reason"] == "gateway_stop"
    usage_dict = done_events[0]["usage"]
    assert usage_dict == {"input_tokens": 4, "output_tokens": 6, "reasoning_tokens": 0}
    assert all(isinstance(value, int) for value in usage_dict.values())


def test_create_stream_retries_once_without_stream_options() -> None:
    fake = _FakeClient(
        [
            _bad_request("unsupported include_usage in stream_options"),
            [_chunk(content="ok"), _chunk(finish_reason="stop")],
        ]
    )

    stream = custom_provider._create_stream(
        cast(Any, fake), {"model": "m", "messages": [], "stream": True}
    )

    events = list(stream)
    assert len(fake.calls) == 2
    assert fake.calls[0]["stream_options"] == {"include_usage": True}
    assert "stream_options" not in fake.calls[1]
    assert events[0].choices[0].delta.content == "ok"


def test_stream_does_not_retry_unrelated_bad_request(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _FakeClient([_bad_request("model is invalid")])
    monkeypatch.setattr(custom_provider, "_client", lambda api_key, base_url: fake)
    provider = custom_provider.CustomProvider()

    with pytest.raises(LLMError) as excinfo:
        _collect(
            provider.stream(
                [{"role": "user", "content": "hello"}],
                model="custom-model",
                tools=None,
                max_tokens=32,
                settings=_settings(),
            )
        )

    assert excinfo.value.code == "bad_request"
    assert len(fake.calls) == 1
    assert fake.calls[0]["stream_options"] == {"include_usage": True}


def test_client_uses_empty_placeholder_for_keyless_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorded: list[dict[str, Any]] = []

    class _Recorder:
        def __init__(self, *, api_key: str, base_url: str) -> None:
            recorded.append({"api_key": api_key, "base_url": base_url})

    custom_provider._client.cache_clear()
    monkeypatch.setattr(custom_provider, "OpenAI", _Recorder)

    custom_provider._client("", "http://gateway.example/v1")

    assert recorded == [{"api_key": "EMPTY", "base_url": "http://gateway.example/v1"}]
    custom_provider._client.cache_clear()


def test_stream_translates_messages_to_chat_completions_shape(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakeClient([[_chunk(finish_reason="stop")]])
    monkeypatch.setattr(custom_provider, "_client", lambda api_key, base_url: fake)
    provider = custom_provider.CustomProvider()
    messages = [
        {"role": "system", "content": "You are helpful."},
        {
            "role": "assistant",
            "content": "Calling tool",
            "tool_calls": [{"id": "call_1", "name": "lookup", "arguments": {"q": "sf"}}],
        },
        {"role": "tool", "tool_call_id": "call_1", "content": {"result": 1}},
        {"role": "user", "content": "Continue"},
    ]

    _collect(
        provider.stream(
            messages,
            model="custom-model",
            tools=None,
            max_tokens=32,
            settings=_settings(),
        )
    )

    assert fake.calls[0]["messages"] == [
        {"role": "system", "content": "You are helpful."},
        {
            "role": "assistant",
            "content": "Calling tool",
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "lookup", "arguments": json.dumps({"q": "sf"})},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "call_1", "content": json.dumps({"result": 1})},
        {"role": "user", "content": "Continue"},
    ]


def test_stream_wraps_tools_in_chat_completions_envelope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakeClient([[_chunk(finish_reason="stop")]])
    monkeypatch.setattr(custom_provider, "_client", lambda api_key, base_url: fake)
    provider = custom_provider.CustomProvider()
    tools = [
        {
            "name": "lookup",
            "description": "Find a record",
            "input_schema": {"type": "object", "properties": {"q": {"type": "string"}}},
        }
    ]

    _collect(
        provider.stream(
            [{"role": "user", "content": "Find it"}],
            model="custom-model",
            tools=tools,
            max_tokens=32,
            settings=_settings(),
        )
    )

    assert fake.calls[0]["tools"] == [
        {
            "type": "function",
            "function": {
                "name": "lookup",
                "description": "Find a record",
                "parameters": {"type": "object", "properties": {"q": {"type": "string"}}},
            },
        }
    ]
