"""Braintrust LLM tracing.

Single seam for instrumenting model calls, tool dispatch, and top-level
flows. Every helper here is a no-op when tracing is disabled in the
admin settings, so callers can scatter ``with trace_flow(...)`` and
``with start_llm_span(...)`` blocks freely without worrying about cost
or failure modes.

To trace a new flow, wrap its outer entry point in a ``trace_flow``
context manager:

    from app.tracing import trace_flow

    def run_my_agent(...):
        with trace_flow("agent.my_agent", input_size=len(text)):
            ...

LLM calls and tool dispatch nest under the active flow span
automatically — they're already instrumented at
``app/llm/client.py:stream`` and ``app/llm/agents/chat.py``.
"""
from __future__ import annotations

from app.tracing.braintrust import (
    is_enabled,
    start_llm_span,
    start_tool_span,
    to_openai_message_shape,
    trace_flow,
)

__all__ = [
    "is_enabled",
    "start_llm_span",
    "start_tool_span",
    "to_openai_message_shape",
    "trace_flow",
]
