"""Tests for app/llm/client.py.

The client owns two responsibilities:

* a *normalized* event stream (``stream()``) — text deltas, completed tool
  calls, end-of-turn — so callers don't branch on provider, and
* a drain helper (``complete()``) that returns the historical
  ``{text, tool_calls, stop_reason, usage}`` dict for one-shot callers.

These tests verify both, plus per-provider translation:

* dispatch by configured provider (and unconfigured / unknown / missing-key),
* Anthropic message + tool translation into ``messages.stream(...)`` kwargs,
* OpenAI translation into the **Responses API** (``responses.create(...)``),
* response normalization across both providers (including tool calls and
  partial-JSON arg accumulation).

The seam under test is the SDK boundary itself, so we substitute fake clients
for ``_client`` in each provider module and capture the kwargs passed to
``messages.stream`` / ``responses.create``. We do not import the real
provider SDKs.
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from app.llm import client as llm_client
from app.llm import settings as llm_settings
from app.llm.errors import LLMError
from app.llm.providers import anthropic as anthropic_provider
from app.llm.providers import openai as openai_provider


def _upsert(**overrides: Any) -> None:
    """upsert() with empty defaults so tests only set fields they care about."""
    base: dict[str, Any] = {
        "provider": "",
        "model": "",
        "anthropic_api_key": "",
        "openai_api_key": "",
        "gemini_api_key": "",
        "ollama_base_url": "",
        "custom_api_key": "",
        "custom_base_url": "",
        "custom_display_name": "",
        "bedrock_aws_region": "",
        "bedrock_endpoint_url": "",
        "bedrock_aws_access_key_id": "",
        "bedrock_aws_secret_access_key": "",
        "bedrock_aws_session_token": "",
        "bedrock_aws_bearer_token": "",
    }
    base.update(overrides)
    llm_settings.upsert(**base)


# --------------------------------------------------------------------------- #
# Anthropic fakes
# --------------------------------------------------------------------------- #


class _FakeAnthropicStreamCtx:
    """Stand-in for the context manager Anthropic's ``messages.stream`` returns."""

    def __init__(self, events, final):
        self._events = events
        self._final = final

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def __iter__(self):
        return iter(self._events)

    def get_final_message(self):
        return self._final


class _FakeAnthropic:
    def __init__(self, events, final):
        self._events = events
        self._final = final
        self.calls: list[dict] = []
        self.messages = SimpleNamespace(stream=self._stream)

    def _stream(self, **kwargs):
        self.calls.append(kwargs)
        return _FakeAnthropicStreamCtx(self._events, self._final)


def _a_text_block_events(text_chunks, *, index=0):
    """Build the event sequence for a single text content block."""
    events = [
        SimpleNamespace(
            type="content_block_start",
            index=index,
            content_block=SimpleNamespace(type="text"),
        ),
    ]
    for chunk in text_chunks:
        events.append(
            SimpleNamespace(
                type="content_block_delta",
                index=index,
                delta=SimpleNamespace(type="text_delta", text=chunk),
            )
        )
    events.append(SimpleNamespace(type="content_block_stop", index=index))
    return events


def _a_tool_use_events(*, index, id, name, arg_chunks):
    events = [
        SimpleNamespace(
            type="content_block_start",
            index=index,
            content_block=SimpleNamespace(type="tool_use", id=id, name=name),
        ),
    ]
    for chunk in arg_chunks:
        events.append(
            SimpleNamespace(
                type="content_block_delta",
                index=index,
                delta=SimpleNamespace(type="input_json_delta", partial_json=chunk),
            )
        )
    events.append(SimpleNamespace(type="content_block_stop", index=index))
    return events


def _a_final(
    *, stop_reason="end_turn", input_tokens=10, output_tokens=20, cache_read=0, cache_write=0
):
    return SimpleNamespace(
        stop_reason=stop_reason,
        usage=SimpleNamespace(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_input_tokens=cache_read,
            cache_creation_input_tokens=cache_write,
        ),
    )


# --------------------------------------------------------------------------- #
# OpenAI (Responses API) fakes
# --------------------------------------------------------------------------- #


class _FakeOpenAI:
    def __init__(self, events):
        self._events = events
        self.calls: list[dict] = []
        self.responses = SimpleNamespace(create=self._create)

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        return iter(self._events)


def _o_text_delta(text):
    return SimpleNamespace(type="response.output_text.delta", delta=text)


def _o_completed(*, status="completed", input_tokens=10, output_tokens=20, cached=0):
    return SimpleNamespace(
        type="response.completed",
        response=SimpleNamespace(
            status=status,
            usage=SimpleNamespace(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                input_tokens_details=SimpleNamespace(cached_tokens=cached),
            ),
        ),
    )


def _o_terminal(etype, *, status, reason="", input_tokens=10, output_tokens=20, cached=0):
    """A terminal Responses event of any kind — ``response.incomplete`` / ``response.failed``
    carry the same payload shape as ``response.completed``, only a different status."""
    response = SimpleNamespace(
        status=status,
        usage=SimpleNamespace(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            input_tokens_details=SimpleNamespace(cached_tokens=cached),
        ),
    )
    if reason:
        response.incomplete_details = SimpleNamespace(reason=reason)
    return SimpleNamespace(type=etype, response=response)


def _o_tool_call_events(*, item_id, call_id, name, arg_chunks):
    events = [
        SimpleNamespace(
            type="response.output_item.added",
            item=SimpleNamespace(
                type="function_call", id=item_id, call_id=call_id, name=name, arguments=""
            ),
        ),
    ]
    for chunk in arg_chunks:
        events.append(
            SimpleNamespace(
                type="response.function_call_arguments.delta",
                item_id=item_id,
                delta=chunk,
            )
        )
    events.append(
        SimpleNamespace(
            type="response.function_call_arguments.done",
            item_id=item_id,
            arguments="".join(arg_chunks),
        )
    )
    return events


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


@pytest.fixture
def configure_anthropic(tmp_db):
    _upsert(
        provider="anthropic",
        model="claude-opus-4-7",
        anthropic_api_key="sk-ant-test",
    )


@pytest.fixture
def configure_openai(tmp_db):
    _upsert(
        provider="openai",
        model="gpt-4o",
        openai_api_key="sk-openai-test",
    )


@pytest.fixture
def fake_anthropic(monkeypatch):
    """Install a fake Anthropic client at the provider's SDK seam.

    ``install(events, final)`` returns the fake so the test can read
    ``fake.calls`` to verify request kwargs.
    """

    def install(events, final):
        fake = _FakeAnthropic(events, final)
        monkeypatch.setattr(anthropic_provider, "_client", lambda api_key: fake)
        return fake

    return install


@pytest.fixture
def fake_openai(monkeypatch):
    def install(events):
        fake = _FakeOpenAI(events)
        monkeypatch.setattr(openai_provider, "_client", lambda api_key: fake)
        return fake

    return install


# --------------------------------------------------------------------------- #
# Configuration / dispatch errors
# --------------------------------------------------------------------------- #


def test_complete_raises_when_provider_unconfigured(tmp_db):
    with pytest.raises(LLMError) as excinfo:
        llm_client.complete([{"role": "user", "content": "hi"}])
    assert excinfo.value.code == "not_configured"
    assert "LLM is not configured" in excinfo.value.message


def test_complete_raises_on_unknown_provider(tmp_db):
    _upsert(provider="cohere", model="x")
    with pytest.raises(LLMError) as excinfo:
        llm_client.complete([{"role": "user", "content": "hi"}])
    assert excinfo.value.code == "not_configured"
    assert "Unknown LLM provider" in excinfo.value.message


def test_complete_raises_when_model_unset(tmp_db):
    _upsert(provider="anthropic", model="", anthropic_api_key="sk-ant")
    with pytest.raises(LLMError) as excinfo:
        llm_client.complete([{"role": "user", "content": "hi"}])
    assert excinfo.value.code == "not_configured"
    assert "No model selected" in excinfo.value.message


def test_complete_raises_when_anthropic_key_unset(tmp_db):
    _upsert(provider="anthropic", model="claude-opus-4-7")
    with pytest.raises(LLMError) as excinfo:
        llm_client.complete([{"role": "user", "content": "hi"}])
    assert excinfo.value.code == "not_configured"
    assert "Anthropic API key" in excinfo.value.message


def test_complete_raises_when_openai_key_unset(tmp_db):
    _upsert(provider="openai", model="gpt-4o")
    with pytest.raises(LLMError) as excinfo:
        llm_client.complete([{"role": "user", "content": "hi"}])
    assert excinfo.value.code == "not_configured"
    assert "OpenAI API key" in excinfo.value.message


def test_complete_raises_when_gemini_key_unset(tmp_db):
    _upsert(provider="gemini", model="gemini-2.5-pro")
    with pytest.raises(LLMError) as excinfo:
        llm_client.complete([{"role": "user", "content": "hi"}])
    assert excinfo.value.code == "not_configured"
    assert "Gemini API key" in excinfo.value.message


def test_complete_uses_settings_model_by_default(configure_anthropic, fake_anthropic):
    fake = fake_anthropic(_a_text_block_events(["ok"]), _a_final())
    llm_client.complete([{"role": "user", "content": "hi"}])
    assert fake.calls[0]["model"] == "claude-opus-4-7"


def test_complete_model_override_takes_precedence(configure_anthropic, fake_anthropic):
    fake = fake_anthropic(_a_text_block_events(["ok"]), _a_final())
    llm_client.complete([{"role": "user", "content": "hi"}], model="claude-haiku-4-5")
    assert fake.calls[0]["model"] == "claude-haiku-4-5"


# --------------------------------------------------------------------------- #
# Anthropic translation (request shape)
# --------------------------------------------------------------------------- #


def test_anthropic_extracts_system_prompt_with_cache_control(
    configure_anthropic, fake_anthropic
):
    fake = fake_anthropic(_a_text_block_events(["hi"]), _a_final())

    llm_client.complete(
        [
            {"role": "system", "content": "be terse"},
            {"role": "user", "content": "hi"},
        ]
    )

    kwargs = fake.calls[0]
    assert kwargs["system"] == [
        {"type": "text", "text": "be terse", "cache_control": {"type": "ephemeral"}}
    ]
    assert kwargs["messages"] == [{"role": "user", "content": "hi"}]


def test_anthropic_concatenates_multiple_system_messages(
    configure_anthropic, fake_anthropic
):
    fake = fake_anthropic(_a_text_block_events(["ok"]), _a_final())

    llm_client.complete(
        [
            {"role": "system", "content": "rule one"},
            {"role": "system", "content": "rule two"},
            {"role": "user", "content": "hi"},
        ]
    )

    assert fake.calls[0]["system"][0]["text"] == "rule one\n\nrule two"


def test_anthropic_no_system_key_when_no_system_message(
    configure_anthropic, fake_anthropic
):
    fake = fake_anthropic(_a_text_block_events(["ok"]), _a_final())
    llm_client.complete([{"role": "user", "content": "hi"}])
    assert "system" not in fake.calls[0]


def test_anthropic_translates_tool_result_message(configure_anthropic, fake_anthropic):
    fake = fake_anthropic(_a_text_block_events(["done"]), _a_final())

    llm_client.complete(
        [
            {"role": "user", "content": "search wiki"},
            {
                "role": "assistant",
                "content": "calling search",
                "tool_calls": [
                    {"id": "tu_1", "name": "search", "arguments": {"q": "foo"}}
                ],
            },
            {"role": "tool", "tool_call_id": "tu_1", "content": "result text"},
        ]
    )

    msgs = fake.calls[0]["messages"]
    assert msgs[1] == {
        "role": "assistant",
        "content": [
            {"type": "text", "text": "calling search"},
            {
                "type": "tool_use",
                "id": "tu_1",
                "name": "search",
                "input": {"q": "foo"},
            },
        ],
    }
    # The final block of a multi-message conversation carries the auto-tail
    # cache breakpoint (see test_anthropic_caches_conversation_tail).
    assert msgs[2] == {
        "role": "user",
        "content": [
            {
                "type": "tool_result",
                "tool_use_id": "tu_1",
                "content": "result text",
                "cache_control": {"type": "ephemeral"},
            }
        ],
    }


def test_anthropic_tool_result_serializes_non_string_content(
    configure_anthropic, fake_anthropic
):
    fake = fake_anthropic(_a_text_block_events(["done"]), _a_final())

    llm_client.complete(
        [
            {"role": "user", "content": "go"},
            {"role": "tool", "tool_call_id": "tu_1", "content": {"hits": [1, 2, 3]}},
        ]
    )

    tool_result = fake.calls[0]["messages"][1]["content"][0]
    assert tool_result["content"] == json.dumps({"hits": [1, 2, 3]})


def test_anthropic_translates_tools_argument(configure_anthropic, fake_anthropic):
    fake = fake_anthropic(_a_text_block_events(["ok"]), _a_final())

    llm_client.complete(
        [{"role": "user", "content": "hi"}],
        tools=[
            {
                "name": "search",
                "description": "search the wiki",
                "input_schema": {"type": "object", "properties": {"q": {"type": "string"}}},
            }
        ],
    )

    assert fake.calls[0]["tools"] == [
        {
            "name": "search",
            "description": "search the wiki",
            "input_schema": {"type": "object", "properties": {"q": {"type": "string"}}},
        }
    ]


def test_anthropic_explicit_cache_marker_breakpoints_that_block(
    configure_anthropic, fake_anthropic
):
    """A message tagged ``cache: True`` gets the breakpoint; the trailing
    (volatile) message does not — explicit marks suppress the auto-tail so the
    ingest stages cache the doc, not the per-batch candidates."""
    fake = fake_anthropic(_a_text_block_events(["ok"]), _a_final())

    llm_client.complete(
        [
            {"role": "system", "content": "instructions"},
            {"role": "user", "content": "the incoming document", "cache": True},
            {"role": "user", "content": "the candidates"},
        ]
    )

    msgs = fake.calls[0]["messages"]
    assert msgs[0] == {
        "role": "user",
        "content": [
            {
                "type": "text",
                "text": "the incoming document",
                "cache_control": {"type": "ephemeral"},
            }
        ],
    }
    # Trailing message is untouched — no stray breakpoint on volatile content.
    assert msgs[1] == {"role": "user", "content": "the candidates"}


def test_anthropic_passes_max_tokens(configure_anthropic, fake_anthropic):
    fake = fake_anthropic(_a_text_block_events(["ok"]), _a_final())
    llm_client.complete([{"role": "user", "content": "hi"}], max_tokens=512)
    assert fake.calls[0]["max_tokens"] == 512


def test_anthropic_single_message_has_no_tail_breakpoint(
    configure_anthropic, fake_anthropic
):
    """A single-shot call only caches the system prompt — there's no prior
    turn to reread, so the auto-tail breakpoint would be a wasted cache write."""
    fake = fake_anthropic(_a_text_block_events(["ok"]), _a_final())
    llm_client.complete([{"role": "user", "content": "hi"}])
    assert fake.calls[0]["messages"] == [{"role": "user", "content": "hi"}]


def test_anthropic_caches_conversation_tail(configure_anthropic, fake_anthropic):
    """Multi-turn conversations with no explicit marks get an auto-tail cache
    breakpoint on the last block, so the growing history is read back instead
    of reprocessed. A string-content tail is promoted to a text block to carry
    the marker."""
    fake = fake_anthropic(_a_text_block_events(["ok"]), _a_final())

    llm_client.complete(
        [
            {"role": "user", "content": "first"},
            {"role": "assistant", "content": "reply"},
            {"role": "user", "content": "second"},
        ]
    )

    msgs = fake.calls[0]["messages"]
    # Earlier turns are untouched; only the final block carries the breakpoint.
    assert msgs[0] == {"role": "user", "content": "first"}
    assert msgs[1] == {"role": "assistant", "content": "reply"}
    assert msgs[-1] == {
        "role": "user",
        "content": [
            {
                "type": "text",
                "text": "second",
                "cache_control": {"type": "ephemeral"},
            }
        ],
    }


# --------------------------------------------------------------------------- #
# Anthropic streaming + normalization
# --------------------------------------------------------------------------- #


def test_anthropic_stream_yields_text_deltas_then_done(
    configure_anthropic, fake_anthropic
):
    fake_anthropic(
        _a_text_block_events(["here ", "you ", "go"]),
        _a_final(input_tokens=12, output_tokens=34),
    )

    events = list(llm_client.stream([{"role": "user", "content": "hi"}]))

    assert events[:3] == [
        {"type": "text_delta", "text": "here "},
        {"type": "text_delta", "text": "you "},
        {"type": "text_delta", "text": "go"},
    ]
    assert events[-1] == {
        "type": "done",
        "stop_reason": "end_turn",
        "usage": {
            "input_tokens": 12,
            "output_tokens": 34,
            "reasoning_tokens": 0,
            "cached_input_tokens": 0,
            "uncached_input_tokens": 12,
        },
    }


def test_anthropic_stream_yields_tool_call_after_arg_chunks(
    configure_anthropic, fake_anthropic
):
    events = (
        _a_text_block_events(["calling…"], index=0)
        + _a_tool_use_events(
            index=1, id="tu_1", name="search", arg_chunks=['{"q":', ' "foo"}']
        )
    )
    fake_anthropic(events, _a_final(stop_reason="tool_use"))

    out = list(llm_client.stream([{"role": "user", "content": "hi"}]))

    assert {"type": "text_delta", "text": "calling…"} in out
    tool_events = [e for e in out if e["type"] == "tool_call"]
    assert tool_events == [
        {
            "type": "tool_call",
            "id": "tu_1",
            "name": "search",
            "arguments": {"q": "foo"},
        }
    ]
    assert out[-1]["type"] == "done"
    assert out[-1]["stop_reason"] == "tool_use"


def test_anthropic_complete_drains_stream_into_dict(
    configure_anthropic, fake_anthropic
):
    events = (
        _a_text_block_events(["here ", "you go"], index=0)
        + _a_tool_use_events(
            index=1, id="tu_1", name="search", arg_chunks=['{"q": "foo"}']
        )
    )
    fake_anthropic(
        events, _a_final(stop_reason="tool_use", input_tokens=12, output_tokens=34)
    )

    out = llm_client.complete([{"role": "user", "content": "hi"}])

    assert out.text == "here you go"
    assert [tc.model_dump() for tc in out.tool_calls] == [
        {"id": "tu_1", "name": "search", "arguments": {"q": "foo"}}
    ]
    assert out.stop_reason == "tool_use"
    assert out.usage.model_dump() == {
        "input_tokens": 12,
        "output_tokens": 34,
        "reasoning_tokens": 0,
        "cached_input_tokens": 0,
        "uncached_input_tokens": 12,
    }


def test_anthropic_usage_splits_cached_and_uncached(
    configure_anthropic, fake_anthropic
):
    # Anthropic's input_tokens excludes cache; cache reads/writes are separate.
    # cached = cache_read; uncached = input_tokens + cache_write.
    fake_anthropic(
        _a_text_block_events(["ok"], index=0),
        _a_final(input_tokens=10, output_tokens=5, cache_read=200, cache_write=50),
    )
    out = llm_client.complete([{"role": "user", "content": "hi"}])
    assert out.usage.input_tokens == 10
    assert out.usage.cached_input_tokens == 200
    assert out.usage.uncached_input_tokens == 60  # 10 fresh + 50 cache write


# --------------------------------------------------------------------------- #
# OpenAI (Responses API) translation
# --------------------------------------------------------------------------- #


def test_openai_lifts_system_to_instructions(configure_openai, fake_openai):
    fake = fake_openai([_o_text_delta("hi"), _o_completed()])

    llm_client.complete(
        [
            {"role": "system", "content": "be terse"},
            {"role": "user", "content": "hi"},
        ]
    )

    kwargs = fake.calls[0]
    assert kwargs["instructions"] == "be terse"
    # System message must NOT appear in the input list — Responses API takes
    # it as a separate ``instructions`` arg.
    assert kwargs["input"] == [{"role": "user", "content": "hi"}]


def test_openai_no_instructions_key_when_no_system(configure_openai, fake_openai):
    fake = fake_openai([_o_text_delta("hi"), _o_completed()])
    llm_client.complete([{"role": "user", "content": "hi"}])
    assert "instructions" not in fake.calls[0]


def test_openai_translates_assistant_with_tool_calls(configure_openai, fake_openai):
    fake = fake_openai([_o_text_delta("ok"), _o_completed()])

    llm_client.complete(
        [
            {"role": "user", "content": "search"},
            {
                "role": "assistant",
                "content": "calling",
                "tool_calls": [
                    {"id": "call_1", "name": "search", "arguments": {"q": "foo"}}
                ],
            },
            {"role": "tool", "tool_call_id": "call_1", "content": "result text"},
        ]
    )

    items = fake.calls[0]["input"]
    # user message
    assert items[0] == {"role": "user", "content": "search"}
    # assistant text → role-style item
    assert items[1] == {"role": "assistant", "content": "calling"}
    # function_call item — Responses API top-level item, not nested
    assert items[2] == {
        "type": "function_call",
        "call_id": "call_1",
        "name": "search",
        "arguments": json.dumps({"q": "foo"}),
    }
    # tool result → function_call_output item
    assert items[3] == {
        "type": "function_call_output",
        "call_id": "call_1",
        "output": "result text",
    }


def test_openai_assistant_with_only_tool_calls_emits_no_text_item(
    configure_openai, fake_openai
):
    """If the assistant turn had no text, only function_call items should be emitted —
    an empty assistant text item would be wasted (and rejected by some providers)."""
    fake = fake_openai([_o_text_delta("ok"), _o_completed()])

    llm_client.complete(
        [
            {"role": "user", "content": "go"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {"id": "call_1", "name": "search", "arguments": {"q": "x"}}
                ],
            },
            {"role": "tool", "tool_call_id": "call_1", "content": "r"},
        ]
    )

    items = fake.calls[0]["input"]
    types = [it.get("type") or it.get("role") for it in items]
    assert types == ["user", "function_call", "function_call_output"]


def test_openai_tool_result_serializes_non_string_content(
    configure_openai, fake_openai
):
    fake = fake_openai([_o_text_delta("ok"), _o_completed()])

    llm_client.complete(
        [
            {"role": "user", "content": "go"},
            {"role": "tool", "tool_call_id": "call_1", "content": {"hits": [1, 2]}},
        ]
    )

    fco = fake.calls[0]["input"][1]
    assert fco["type"] == "function_call_output"
    assert fco["output"] == json.dumps({"hits": [1, 2]})


def test_openai_uses_flat_function_tool_envelope(configure_openai, fake_openai):
    """Responses API tools are flat ``{type: function, name, description, parameters}``,
    NOT chat.completions' nested ``{type: function, function: {...}}``."""
    fake = fake_openai([_o_text_delta("ok"), _o_completed()])

    llm_client.complete(
        [{"role": "user", "content": "hi"}],
        tools=[
            {
                "name": "search",
                "description": "search the wiki",
                "input_schema": {"type": "object"},
            }
        ],
    )

    assert fake.calls[0]["tools"] == [
        {
            "type": "function",
            "name": "search",
            "description": "search the wiki",
            "parameters": {"type": "object"},
        }
    ]


def test_openai_passes_max_output_tokens_and_stream_flag(
    configure_openai, fake_openai
):
    fake = fake_openai([_o_text_delta("ok"), _o_completed()])
    llm_client.complete([{"role": "user", "content": "hi"}], max_tokens=512)
    assert fake.calls[0]["max_output_tokens"] == 512
    assert fake.calls[0]["stream"] is True


# --------------------------------------------------------------------------- #
# OpenAI streaming + normalization
# --------------------------------------------------------------------------- #


def test_openai_stream_yields_text_deltas(configure_openai, fake_openai):
    fake_openai(
        [
            _o_text_delta("here "),
            _o_text_delta("you "),
            _o_text_delta("go"),
            _o_completed(input_tokens=7, output_tokens=11),
        ]
    )

    events = list(llm_client.stream([{"role": "user", "content": "hi"}]))

    assert events == [
        {"type": "text_delta", "text": "here "},
        {"type": "text_delta", "text": "you "},
        {"type": "text_delta", "text": "go"},
        {
            "type": "done",
            "stop_reason": "completed",
            "usage": {
                "input_tokens": 7,
                "output_tokens": 11,
                "reasoning_tokens": 0,
                "cached_input_tokens": 0,
                "uncached_input_tokens": 7,
            },
        },
    ]


def test_openai_stream_emits_tool_call_with_call_id_not_item_id(
    configure_openai, fake_openai
):
    """The id we surface in ``tool_call`` events must be the ``call_id``
    (used to echo back ``function_call_output``), not the internal ``item_id``."""
    fake_openai(
        _o_tool_call_events(
            item_id="fc_internal",
            call_id="call_1",
            name="search",
            arg_chunks=['{"q":', ' "foo"}'],
        )
        + [_o_completed()]
    )

    events = list(llm_client.stream([{"role": "user", "content": "hi"}]))

    tool_events = [e for e in events if e["type"] == "tool_call"]
    assert tool_events == [
        {
            "type": "tool_call",
            "id": "call_1",
            "name": "search",
            "arguments": {"q": "foo"},
        }
    ]


def test_openai_unparseable_tool_arguments_fall_back_to_raw(
    configure_openai, fake_openai
):
    fake_openai(
        _o_tool_call_events(
            item_id="fc_1",
            call_id="call_1",
            name="search",
            arg_chunks=["not-json{"],
        )
        + [_o_completed()]
    )

    out = llm_client.complete([{"role": "user", "content": "hi"}])

    assert [tc.model_dump() for tc in out.tool_calls] == [
        {"id": "call_1", "name": "search", "arguments": {"_raw": "not-json{"}}
    ]


def test_openai_complete_handles_missing_usage(configure_openai, fake_openai):
    completed = SimpleNamespace(
        type="response.completed",
        response=SimpleNamespace(status="completed", usage=None),
    )
    fake_openai([_o_text_delta("ok"), completed])

    out = llm_client.complete([{"role": "user", "content": "hi"}])

    assert out.usage.model_dump() == {
        "input_tokens": 0,
        "output_tokens": 0,
        "reasoning_tokens": 0,
        "cached_input_tokens": 0,
        "uncached_input_tokens": 0,
    }
    assert out.text == "ok"


def test_openai_usage_splits_cached_and_uncached(configure_openai, fake_openai):
    # OpenAI's input_tokens INCLUDES cached; uncached = input_tokens - cached.
    fake_openai([_o_text_delta("ok"), _o_completed(input_tokens=100, output_tokens=5, cached=80)])
    out = llm_client.complete([{"role": "user", "content": "hi"}])
    assert out.usage.input_tokens == 100
    assert out.usage.cached_input_tokens == 80
    assert out.usage.uncached_input_tokens == 20


# --------------------------------------------------------------------------- #
# Truncated / abnormal responses
#
# A response cut short at ``max_output_tokens`` ends with ``response.incomplete``, not
# ``response.completed``. Handling only the latter returned the partial text with an empty
# stop_reason and zero usage — indistinguishable from a whole answer, and no exception — which is
# how entity-type extraction silently lost every large wiki page. Verified against the live API:
# the SDK's final event was ``response.incomplete`` while the caller saw stop_reason=''.
# --------------------------------------------------------------------------- #


def test_openai_reports_a_truncated_response_as_incomplete(configure_openai, fake_openai):
    fake_openai(
        [
            _o_text_delta('{"referents":[{"name":"Acme"}'),
            _o_terminal("response.incomplete", status="incomplete", reason="max_output_tokens"),
        ]
    )

    result = llm_client.complete([{"role": "user", "content": "extract"}])

    assert result.stop_reason == "incomplete"
    assert result.text == '{"referents":[{"name":"Acme"}'


def test_openai_reports_usage_for_a_truncated_response(configure_openai, fake_openai):
    """Usage is on the terminal event whatever its status, so dropping the event also lost the
    token counts — which is what made a truncated call look like it had never happened."""
    fake_openai(
        [
            _o_text_delta("partial"),
            _o_terminal(
                "response.incomplete", status="incomplete", input_tokens=51000, output_tokens=4096
            ),
        ]
    )

    result = llm_client.complete([{"role": "user", "content": "extract"}])

    assert result.usage.input_tokens == 51000
    assert result.usage.output_tokens == 4096


def test_openai_reports_a_failed_response(configure_openai, fake_openai):
    fake_openai([_o_text_delta("half"), _o_terminal("response.failed", status="failed")])

    result = llm_client.complete([{"role": "user", "content": "extract"}])

    assert result.stop_reason == "failed"


def test_openai_still_reports_a_completed_response(configure_openai, fake_openai):
    """The success path must be untouched by widening the terminal branch."""
    fake_openai([_o_text_delta("done"), _o_completed()])

    result = llm_client.complete([{"role": "user", "content": "hi"}])

    assert result.stop_reason == "completed"
    assert result.usage.input_tokens == 10


def test_a_stream_with_no_terminal_event_is_reported_incomplete(
    configure_openai, fake_openai, caplog
):
    """The safety net under the provider fix: a dropped connection, or any future status this
    layer does not translate, must not present partial text as a finished answer."""
    fake_openai([_o_text_delta("cut off here")])

    with caplog.at_level("WARNING"):
        result = llm_client.complete([{"role": "user", "content": "extract"}])

    assert result.stop_reason == "incomplete"
    assert result.text == "cut off here"
    assert any("no terminal event" in r.getMessage() for r in caplog.records)
