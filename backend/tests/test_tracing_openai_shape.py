"""``to_openai_message_shape`` must translate our internal message format to
the OpenAI chat-completions shape Braintrust's UI renders. Without this,
assistant turns that only call tools surface as empty bubbles and the call
arguments don't appear at all."""
from __future__ import annotations

import json

from app.tracing import to_openai_message_shape


def test_passthrough_for_simple_text_messages():
    msgs = [
        {"role": "system", "content": "be terse"},
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
    ]
    assert to_openai_message_shape(msgs) == msgs


def test_assistant_with_tool_calls_translates_to_openai_shape():
    msgs = [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"id": "call_1", "name": "echo", "arguments": {"x": 1, "y": "z"}}
            ],
        }
    ]
    out = to_openai_message_shape(msgs)
    assert out == [
        {
            "role": "assistant",
            # Empty content becomes None so Braintrust doesn't render an
            # empty assistant bubble next to the tool call.
            "content": None,
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {
                        "name": "echo",
                        "arguments": json.dumps({"x": 1, "y": "z"}),
                    },
                }
            ],
        }
    ]


def test_assistant_with_text_and_tool_calls_keeps_content():
    msgs = [
        {
            "role": "assistant",
            "content": "let me check",
            "tool_calls": [
                {"id": "c1", "name": "search", "arguments": {"q": "hi"}}
            ],
        }
    ]
    out = to_openai_message_shape(msgs)
    assert out[0]["content"] == "let me check"
    assert out[0]["tool_calls"][0]["function"]["arguments"] == json.dumps({"q": "hi"})


def test_tool_messages_pass_through_unchanged():
    msgs = [{"role": "tool", "tool_call_id": "call_1", "content": "result text"}]
    assert to_openai_message_shape(msgs) == msgs


def test_string_arguments_are_not_double_encoded():
    """If arguments is already a JSON string (some replay paths), keep it as-is."""
    msgs = [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"id": "c1", "name": "echo", "arguments": '{"x":1}'}
            ],
        }
    ]
    out = to_openai_message_shape(msgs)
    assert out[0]["tool_calls"][0]["function"]["arguments"] == '{"x":1}'


def test_full_tool_use_conversation_round_trip():
    msgs = [
        {"role": "user", "content": "use the tool"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"id": "c1", "name": "echo", "arguments": {"x": 1}}
            ],
        },
        {"role": "tool", "tool_call_id": "c1", "content": '{"echoed": {"x": 1}}'},
        {"role": "assistant", "content": "done"},
    ]
    out = to_openai_message_shape(msgs)
    assert [m["role"] for m in out] == ["user", "assistant", "tool", "assistant"]
    assert out[1]["content"] is None
    assert out[1]["tool_calls"][0]["id"] == "c1"
    assert out[1]["tool_calls"][0]["function"]["name"] == "echo"
    assert out[2] == msgs[2]
    assert out[3] == msgs[3]
