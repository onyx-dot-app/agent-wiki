"""Braintrust SDK glue — lazy logger init + no-op-when-disabled span helpers.

The admin-configured ``BraintrustSettings`` row is the only source of
truth (no env-var fallback). Every public helper here:

* fetches the current settings on each call (cheap relative to LLM
  latency — same as ``app/llm/settings.get``),
* lazily initializes ``braintrust.init_logger`` and caches the result
  keyed by ``(project, api_key)`` so admin updates rotate cleanly,
* yields ``None`` when tracing is disabled, missing config, or any
  unexpected error during init — instrumentation must never break the
  main code path.

The Braintrust SDK uses contextvars internally for parent/child span
resolution, so spans started inside an active ``with trace_flow(...)``
or ``with start_llm_span(...)`` block automatically nest. Callers don't
need to thread parents.
"""
from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Any, Generator

from app.tracing import settings as tracing_settings

log = logging.getLogger(__name__)

# Cache: ((project, api_key), Logger). Keyed by the credential tuple so
# rotating the project or key on the admin page replaces the cached
# logger transparently.
_LoggerCache = tuple[tuple[str, str], Any]
_logger_cache: _LoggerCache | None = None


def _get_logger() -> Any | None:
    """Return the active Braintrust logger, or None if tracing is off.

    Logger instantiation is cached for the lifetime of the
    ``(project, api_key)`` tuple. Settings are re-read on every call;
    that's intentional so admin toggles take effect immediately. The
    DB read is one indexed lookup against a single-row table — orders
    of magnitude cheaper than the LLM call this wraps.

    Any error (DB unavailable, SDK init failure) yields ``None`` —
    instrumentation must never break the main code path. Tests run
    without a configured DB; this is the seam that keeps them green.
    """
    try:
        s = tracing_settings.get()
    except Exception:
        return None
    if not (s.enabled and s.project and s.api_key):
        return None
    cache_key = (s.project, s.api_key)
    global _logger_cache
    if _logger_cache is not None and _logger_cache[0] == cache_key:
        return _logger_cache[1]
    try:
        import braintrust
        logger = braintrust.init_logger(project=s.project, api_key=s.api_key)
    except Exception:
        log.exception("braintrust init_logger failed; disabling tracing for this call")
        return None
    _logger_cache = (cache_key, logger)
    return logger


def is_enabled() -> bool:
    return _get_logger() is not None


@contextmanager
def trace_flow(name: str, **metadata: Any) -> Generator[Any | None, None, None]:
    """Open a root span for a user-facing flow.

    Use this at the entry of any operation worth tracing as a unit —
    a chat turn, a doc-update agent run, a background title-generation
    task. LLM and tool spans created inside the block nest under it
    automatically.
    """
    logger = _get_logger()
    if logger is None:
        yield None
        return
    try:
        span_cm: Any = logger.start_span(name=name, metadata=metadata or None)
    except Exception:
        log.exception("braintrust start_span failed; continuing without span")
        yield None
        return
    with span_cm as span:
        yield span


@contextmanager
def start_llm_span(
    *,
    provider: str,
    model: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None,
    max_tokens: int,
) -> Generator[Any | None, None, None]:
    """Wrap a single ``provider.stream(...)`` invocation.

    Logs the inbound messages + available tools immediately and lets
    the caller ``.log(output=..., metadata=...)`` final results once
    the stream has drained.
    """
    logger = _get_logger()
    if logger is None:
        yield None
        return
    try:
        import braintrust
        from braintrust.span_types import SpanTypeAttribute
        span_cm: Any = braintrust.start_span(
            name=f"llm:{provider}",
            type=SpanTypeAttribute.LLM,
            input=messages,
            metadata={
                "provider": provider,
                "model": model,
                "max_tokens": max_tokens,
                "tools": tools,
            },
        )
    except Exception:
        log.exception("braintrust start_span (llm) failed; continuing without span")
        yield None
        return
    with span_cm as span:
        yield span


@contextmanager
def start_tool_span(
    *,
    name: str,
    arguments: dict[str, Any],
) -> Generator[Any | None, None, None]:
    """Wrap a tool dispatch from inside the agent loop."""
    logger = _get_logger()
    if logger is None:
        yield None
        return
    try:
        import braintrust
        from braintrust.span_types import SpanTypeAttribute
        span_cm: Any = braintrust.start_span(
            name=f"tool:{name}",
            type=SpanTypeAttribute.TOOL,
            input=arguments,
        )
    except Exception:
        log.exception("braintrust start_span (tool) failed; continuing without span")
        yield None
        return
    with span_cm as span:
        yield span


def _reset_cache_for_tests() -> None:  # pyright: ignore[reportUnusedFunction]
    """Test hook — drop the cached logger so a fresh ``get()`` re-resolves."""
    global _logger_cache
    _logger_cache = None
