"""Tests for ``app/triggers/natural_language.py``.

Patches the LLM seam (``complete``) inside the module under test rather than
the SDK clients — this evaluator only cares about the normalized response
shape, so the seam-level patch is the right level.
"""
from __future__ import annotations


def _stub_complete(tool_calls):
    return lambda *args, **kwargs: {
        "text": "",
        "tool_calls": tool_calls,
        "stop_reason": "tool_use",
        "usage": {"input_tokens": 1, "output_tokens": 1},
        "raw": None,
    }


def test_matches_returns_verdict_from_tool_call(monkeypatch):
    from app.triggers import natural_language

    captured: dict = {}

    def fake_complete(messages, *, tools=None, max_tokens=512, model=None):
        captured["messages"] = messages
        captured["tools"] = tools
        return {
            "text": "",
            "tool_calls": [
                {
                    "id": "1",
                    "name": "report",
                    "arguments": {"matches": True, "reason": "Status flipped to yellow"},
                }
            ],
            "stop_reason": "tool_use",
            "usage": {"input_tokens": 1, "output_tokens": 1},
            "raw": None,
        }

    monkeypatch.setattr(natural_language, "complete", fake_complete)
    matched, reason = natural_language.matches(
        "fire when status flips",
        "status: green",
        "status: yellow",
        change_kind="edit",
    )
    assert matched is True
    assert "yellow" in reason

    user_msg = captured["messages"][-1]["content"]
    assert "edit" in user_msg
    assert "status: green" in user_msg
    assert "status: yellow" in user_msg
    assert captured["tools"][0]["name"] == "report"


def test_matches_false_when_no_tool_call(monkeypatch):
    from app.triggers import natural_language

    monkeypatch.setattr(natural_language, "complete", _stub_complete([]))
    matched, reason = natural_language.matches("x", "a", "b", change_kind="edit")
    assert matched is False
    assert reason == "no_tool_call"


def test_matches_returns_false_on_llm_error(monkeypatch):
    from app.llm.client import LLMError
    from app.triggers import natural_language

    def boom(*a, **kw):
        raise LLMError("not_configured", "no provider")

    monkeypatch.setattr(natural_language, "complete", boom)
    matched, reason = natural_language.matches("x", "a", "b", change_kind="edit")
    assert matched is False
    assert reason.startswith("llm_error:")
