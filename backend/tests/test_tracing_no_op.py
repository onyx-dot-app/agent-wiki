"""Tracing helpers must be safe to scatter — when disabled they yield None
and never import the Braintrust SDK or raise. This locks that contract."""
from __future__ import annotations

from app.tracing import is_enabled, start_llm_span, start_tool_span, trace_flow
from app.tracing import braintrust as bt_module
from app.tracing import settings as braintrust_settings


def test_is_enabled_false_when_no_row(tmp_db):
    bt_module._reset_cache_for_tests()
    assert is_enabled() is False


def test_is_enabled_false_when_enabled_flag_off(tmp_db):
    braintrust_settings.upsert(project="p", api_key="k", enabled=False)
    bt_module._reset_cache_for_tests()
    assert is_enabled() is False


def test_is_enabled_false_when_project_blank(tmp_db):
    braintrust_settings.upsert(project="", api_key="k", enabled=True)
    bt_module._reset_cache_for_tests()
    assert is_enabled() is False


def test_is_enabled_false_when_key_blank(tmp_db):
    braintrust_settings.upsert(project="p", api_key="", enabled=True)
    bt_module._reset_cache_for_tests()
    assert is_enabled() is False


def test_trace_flow_yields_none_when_disabled(tmp_db):
    bt_module._reset_cache_for_tests()
    with trace_flow("any.flow", k="v") as span:
        assert span is None


def test_start_llm_span_yields_none_when_disabled(tmp_db):
    bt_module._reset_cache_for_tests()
    with start_llm_span(
        provider="anthropic",
        model="claude",
        messages=[{"role": "user", "content": "hi"}],
        tools=None,
        max_tokens=100,
    ) as span:
        assert span is None


def test_start_tool_span_yields_none_when_disabled(tmp_db):
    bt_module._reset_cache_for_tests()
    with start_tool_span(name="search_wiki", arguments={"q": "x"}) as span:
        assert span is None


def test_trace_flow_does_not_break_caller_on_init_failure(tmp_db, monkeypatch):
    """If init_logger raises, the helper logs and yields None — caller proceeds."""
    braintrust_settings.upsert(project="p", api_key="k", enabled=True)
    bt_module._reset_cache_for_tests()

    def _boom(**_):
        raise RuntimeError("network down")

    import braintrust
    monkeypatch.setattr(braintrust, "init_logger", _boom)
    with trace_flow("any.flow") as span:
        assert span is None


def test_helpers_compose_and_yield_none_chained(tmp_db):
    """Nested context managers must all yield None cleanly when disabled."""
    bt_module._reset_cache_for_tests()
    with trace_flow("outer") as outer, start_llm_span(
        provider="anthropic", model="m", messages=[], tools=None, max_tokens=10
    ) as inner, start_tool_span(name="t", arguments={}) as tool:
        assert outer is None
        assert inner is None
        assert tool is None
