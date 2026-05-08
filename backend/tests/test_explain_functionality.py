"""Tests for the `explain_functionality` tool."""
from __future__ import annotations


def test_returns_app_help_content():
    from app.llm.agents.tools.explain_functionality import handle

    out = handle({})
    assert "error" not in out
    assert isinstance(out["content"], str)
    # Sanity-check that the canonical blurb is what's returned, not some
    # stale or wrong file. Anchor on a stable section header.
    assert "agent-wiki" in out["content"]
    assert "## Triggers and events" in out["content"]


def test_takes_no_arguments_and_ignores_extras():
    """Spec says no arguments, but extras shouldn't break the handler."""
    from app.llm.agents.tools.explain_functionality import handle

    out = handle({"unexpected": "ignored"})
    assert "error" not in out
    assert out["content"]


def test_registered_in_tool_registry():
    from app.llm.agents import tools

    names = {s["name"] for s in tools.TOOL_SPECS}
    assert "explain_functionality" in names

    out = tools.dispatch("explain_functionality", {})
    assert out["content"]
