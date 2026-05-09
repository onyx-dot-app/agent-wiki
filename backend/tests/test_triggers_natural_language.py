"""Tests for ``app/triggers/natural_language.py``.

Patches the LLM seam (``complete``) inside the module under test rather than
the SDK clients — these helpers only care about the normalized response
shape, so the seam-level patch is the right level.
"""
from __future__ import annotations

from app.llm.client import CompletionResult, ToolCall, Usage


def _stub_complete(tool_calls):
    calls = [ToolCall(**c) for c in tool_calls]
    return lambda *args, **kwargs: CompletionResult(
        text="",
        tool_calls=calls,
        stop_reason="tool_use",
        usage=Usage(input_tokens=1, output_tokens=1),
    )


# --------------------------------------------------------------------------- #
# matches() — phase 1                                                         #
# --------------------------------------------------------------------------- #


def test_matches_returns_verdict_from_tool_call(monkeypatch):
    from app.triggers import natural_language

    captured: dict = {}

    def fake_complete(messages, *, tools=None, max_tokens=512, model=None):
        captured["messages"] = messages
        captured["tools"] = tools
        return CompletionResult(
            text="",
            tool_calls=[
                ToolCall(id="1", name="report", arguments={"matches": True, "reason": "Status flipped to yellow"})
            ],
            stop_reason="tool_use",
            usage=Usage(input_tokens=1, output_tokens=1),
        )

    monkeypatch.setattr(natural_language, "complete", fake_complete)

    payload = (
        "=== WIKI (latest version) ===\n"
        "--- projects/foo.md\nstatus: yellow\n\n"
        "=== CHANGE ===\nPath: projects/foo.md\nKind: edit\n\n"
        "<unified diff>\n-status: green\n+status: yellow\n</unified diff>\n"
    )
    res = natural_language.matches("fire when status flips", payload)
    matched, reason = res.matched, res.reason
    assert matched is True
    assert "yellow" in reason

    user_msg = captured["messages"][-1]["content"]
    assert "Trigger description" in user_msg
    assert "WIKI (latest version)" in user_msg
    assert "CHANGE" in user_msg
    assert captured["tools"][0]["name"] == "report"


def test_matches_system_prompt_mentions_diff_focus(monkeypatch):
    from app.triggers import natural_language

    captured: dict = {}

    def fake_complete(messages, *, tools=None, max_tokens=512, model=None):
        captured["system"] = messages[0]["content"]
        return CompletionResult(
            text="",
            tool_calls=[
                ToolCall(id="1", name="report", arguments={"matches": False, "reason": "no signal"})
            ],
            stop_reason="tool_use",
            usage=Usage(input_tokens=1, output_tokens=1),
        )

    monkeypatch.setattr(natural_language, "complete", fake_complete)
    natural_language.matches("x", "payload")
    sys = captured["system"]
    assert "diff" in sys.lower()
    assert "state" in sys.lower()


def test_matches_false_when_no_tool_call(monkeypatch):
    from app.triggers import natural_language

    monkeypatch.setattr(natural_language, "complete", _stub_complete([]))
    res = natural_language.matches("x", "payload")
    matched, reason = res.matched, res.reason
    assert matched is False
    assert reason == "no_tool_call"


def test_matches_returns_false_on_llm_error(monkeypatch):
    from app.llm.errors import LLMError
    from app.triggers import natural_language

    def boom(*a, **kw):
        raise LLMError("not_configured", "no provider")

    monkeypatch.setattr(natural_language, "complete", boom)
    res = natural_language.matches("x", "payload")
    matched, reason = res.matched, res.reason
    assert matched is False
    assert reason.startswith("llm_error:")


# --------------------------------------------------------------------------- #
# render_message() — phase 2                                                  #
# --------------------------------------------------------------------------- #


def test_render_message_returns_text_from_tool_call(monkeypatch):
    from app.triggers import natural_language

    captured: dict = {}

    def fake_complete(messages, *, tools=None, max_tokens=1024, model=None):
        captured["messages"] = messages
        captured["tools"] = tools
        return CompletionResult(
            text="",
            tool_calls=[
                ToolCall(id="1", name="render", arguments={
                        "message": "projects/foo.md flipped from green to yellow."
                    })
            ],
            stop_reason="tool_use",
            usage=Usage(input_tokens=1, output_tokens=1),
        )

    monkeypatch.setattr(natural_language, "complete", fake_complete)
    out = natural_language.render_message(
        "tell me when status changes",
        "PAYLOAD",
        reason="Status flipped to yellow",
    )
    assert out == "projects/foo.md flipped from green to yellow."

    user_msg = captured["messages"][-1]["content"]
    assert "tell me when status changes" in user_msg
    assert "Status flipped to yellow" in user_msg
    assert "PAYLOAD" in user_msg
    assert captured["tools"][0]["name"] == "render"


def test_render_message_falls_back_to_instruction_on_no_tool_call(monkeypatch):
    from app.triggers import natural_language

    monkeypatch.setattr(natural_language, "complete", _stub_complete([]))
    out = natural_language.render_message("raw template", "PAYLOAD", reason="r")
    assert out == "raw template"


def test_render_message_falls_back_on_llm_error(monkeypatch):
    from app.llm.errors import LLMError
    from app.triggers import natural_language

    def boom(*a, **kw):
        raise LLMError("not_configured", "no provider")

    monkeypatch.setattr(natural_language, "complete", boom)
    out = natural_language.render_message("raw template", "PAYLOAD", reason="r")
    assert out == "raw template"


def test_render_message_strips_whitespace(monkeypatch):
    from app.triggers import natural_language

    monkeypatch.setattr(
        natural_language,
        "complete",
        _stub_complete(
            [
                {
                    "id": "1",
                    "name": "render",
                    "arguments": {"message": "  hello  \n"},
                }
            ]
        ),
    )
    assert natural_language.render_message("x", "y", reason="z") == "hello"


# --------------------------------------------------------------------------- #
# evaluate_new_file_in_dir() — single JSON-output call                        #
# --------------------------------------------------------------------------- #


def _stub_text(text: str):
    """Stub that returns ``text`` as the assistant's text content."""
    return lambda *a, **kw: CompletionResult(
        text=text,
        tool_calls=[],
        stop_reason="end_turn",
        usage=Usage(input_tokens=1, output_tokens=1),
    )


def test_new_file_in_dir_parses_json_object(monkeypatch):
    from app.triggers import natural_language

    captured: dict = {}

    def fake_complete(messages, *, tools=None, max_tokens=1024, model=None):
        captured["messages"] = messages
        captured["tools"] = tools
        return CompletionResult(
            text='{"triggered": true, "trigger_message": "New project Foo added."}',
            tool_calls=[],
            stop_reason="end_turn",
            usage=Usage(input_tokens=1, output_tokens=1),
        )

    monkeypatch.setattr(natural_language, "complete", fake_complete)

    res = natural_language.evaluate_new_file_in_dir(
        "fire when a new project doc lands",
        "tell me about new projects",
        "=== WIKI (latest version) ===\n\n=== NEW FILE ===\nPath: p/foo.md\n\n# Foo\n",
    )
    triggered, msg = res.triggered, res.message
    assert triggered is True
    assert msg == "New project Foo added."

    user_msg = captured["messages"][-1]["content"]
    assert "Trigger description" in user_msg
    assert "Message instruction" in user_msg
    assert "NEW FILE" in user_msg
    # No tools — this path uses raw JSON output, not a tool call
    assert captured["tools"] is None


def test_new_file_in_dir_strips_markdown_fence(monkeypatch):
    from app.triggers import natural_language

    monkeypatch.setattr(
        natural_language,
        "complete",
        _stub_text(
            '```json\n{"triggered": false, "trigger_message": ""}\n```'
        ),
    )
    res = natural_language.evaluate_new_file_in_dir("x", "y", "p")
    triggered, msg = res.triggered, res.message
    assert triggered is False
    assert msg == ""


def test_new_file_in_dir_tolerates_surrounding_text(monkeypatch):
    """Model occasionally adds prose around the JSON; we still recover."""
    from app.triggers import natural_language

    monkeypatch.setattr(
        natural_language,
        "complete",
        _stub_text(
            'Sure! Here is my response:\n'
            '{"triggered": true, "trigger_message": "ok"}\n'
            'Let me know if you need anything else.'
        ),
    )
    res = natural_language.evaluate_new_file_in_dir("x", "y", "p")
    triggered, msg = res.triggered, res.message
    assert triggered is True
    assert msg == "ok"


def test_new_file_in_dir_falls_back_when_unparseable(monkeypatch):
    from app.triggers import natural_language

    monkeypatch.setattr(natural_language, "complete", _stub_text("not json at all"))
    res = natural_language.evaluate_new_file_in_dir("x", "y", "p")
    triggered, msg = res.triggered, res.message
    assert triggered is False
    assert msg == ""


def test_new_file_in_dir_falls_back_on_llm_error(monkeypatch):
    from app.llm.errors import LLMError
    from app.triggers import natural_language

    def boom(*a, **kw):
        raise LLMError("not_configured", "no provider")

    monkeypatch.setattr(natural_language, "complete", boom)
    res = natural_language.evaluate_new_file_in_dir("x", "y", "p")
    triggered, msg = res.triggered, res.message
    assert triggered is False
    assert msg == ""


def test_new_file_in_dir_uses_instruction_when_message_empty(monkeypatch):
    """If the model says triggered=true but leaves the message blank, fall
    back to the owner's raw instruction so we don't drop a fire."""
    from app.triggers import natural_language

    monkeypatch.setattr(
        natural_language,
        "complete",
        _stub_text('{"triggered": true, "trigger_message": ""}'),
    )
    res = natural_language.evaluate_new_file_in_dir(
        "if-clause", "raw instruction", "p"
    )
    triggered, msg = res.triggered, res.message
    assert triggered is True
    assert msg == "raw instruction"
