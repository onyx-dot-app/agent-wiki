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

import json
import logging
from contextlib import contextmanager
from typing import Any, Generator

from pydantic import BaseModel, ConfigDict

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


def to_openai_message_shape(
    messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Translate our normalized message shape to OpenAI Chat Completions shape.

    Braintrust's UI renders LLM span input/output as OpenAI chat-completions
    messages: assistant ``tool_calls`` must be ``[{id, type: "function",
    function: {name, arguments: "<json str>"}}]``. Our internal shape uses
    ``[{id, name, arguments: <dict>}]``, which Braintrust doesn't recognize —
    so without this conversion, assistant turns that *only* call tools render
    as empty bubbles and the call arguments don't appear at all. Tool result
    messages already match (``{role: "tool", tool_call_id, content}``).
    """
    out: list[dict[str, Any]] = []
    for m in messages:
        if m.get("role") == "assistant" and m.get("tool_calls"):
            tool_calls: list[dict[str, Any]] = []
            for tc in m["tool_calls"]:
                args = tc.get("arguments", {})
                args_str = args if isinstance(args, str) else json.dumps(args)
                tool_calls.append(
                    {
                        "id": tc["id"],
                        "type": "function",
                        "function": {"name": tc["name"], "arguments": args_str},
                    }
                )
            content = m.get("content", "")
            out.append(
                {
                    "role": "assistant",
                    # OpenAI uses null when the assistant only called tools;
                    # this keeps Braintrust from rendering an empty text bubble.
                    "content": content if content else None,
                    "tool_calls": tool_calls,
                }
            )
        else:
            out.append(m)
    return out


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
            input=to_openai_message_shape(messages),
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


class ExperimentRow(BaseModel):
    """One row pushed to a Braintrust experiment.

    Eval surfaces (``backend/evals``) build these rows from per-case
    results. Keeping the shape here (rather than in evals) means callers
    can't reach for the ``braintrust`` SDK directly — they go through
    ``push_experiment``, which is the only allowed entry point.
    """

    model_config = ConfigDict(frozen=True)

    input: dict[str, Any]
    output: dict[str, Any]
    expected: dict[str, Any]
    scores: dict[str, float]
    metadata: dict[str, Any]


class DatasetRow(BaseModel):
    """One row pushed to a Braintrust dataset.

    Mirrors the BT dataset row shape: ``input`` is what the model sees,
    ``expected`` is the ground-truth answer, ``metadata`` carries dataset-
    level tags. The optional ``id`` lets callers anchor a row to a stable
    identifier (e.g. a case_id) so subsequent dataset versions can update
    the same row instead of appending a duplicate.
    """

    model_config = ConfigDict(frozen=True)

    input: dict[str, Any]
    expected: dict[str, Any]
    metadata: dict[str, Any]
    id: str | None = None


def push_dataset(
    *,
    project: str,
    dataset: str,
    api_key: str,
    rows: list[DatasetRow],
) -> int:
    """Upload ``rows`` to a Braintrust dataset (versioned, idempotent).

    Each call creates or updates the named dataset. Rows with a stable
    ``id`` are upserted — re-running with the same cases produces a new
    dataset version rather than appending duplicates. Experiments can
    then link results to dataset row ids for cross-run regression
    comparison in the BT UI.
    """
    if not rows:
        return 0
    try:
        import braintrust
    except ImportError:
        log.warning("braintrust SDK not installed; skipping dataset push")
        return 0
    try:
        bt_dataset: Any = braintrust.init_dataset(
            project=project,
            name=dataset,
            api_key=api_key,
        )
        for row in rows:
            insert_kwargs: dict[str, Any] = {
                "input": row.input,
                "expected": row.expected,
                "metadata": row.metadata,
            }
            if row.id is not None:
                insert_kwargs["id"] = row.id
            bt_dataset.insert(**insert_kwargs)
        bt_dataset.flush()
    except Exception:
        log.exception("braintrust dataset push failed; rows not uploaded")
        return 0
    return len(rows)


def push_experiment(
    *,
    project: str,
    experiment: str,
    api_key: str,
    rows: list[ExperimentRow],
    dataset: str | None = None,
) -> int:
    """Upload ``rows`` to a Braintrust experiment. Returns count uploaded.

    Soft-fails on SDK or network errors with a warning so an unreachable
    Braintrust never breaks an eval run. Returns 0 if the SDK isn't
    installed or any error occurs after init.

    The eval surfaces are the only callers. Live-tracing
    (``trace_flow`` / ``start_llm_span`` / ``start_tool_span``) uses the
    DB-backed admin settings and a separate logger; this helper takes
    explicit args because eval runs configure their own credentials via
    env vars and don't share the admin row.
    """
    if not rows:
        return 0
    try:
        import braintrust
    except ImportError:
        log.warning("braintrust SDK not installed; skipping experiment push")
        return 0
    try:
        # When ``dataset`` is set, link the experiment to that BT dataset so
        # the UI can show per-row regression vs prior experiments using the
        # same dataset. ``case_id`` in each row's metadata is also used as
        # the dataset record id at log time.
        bt_dataset: Any = None
        if dataset is not None:
            bt_dataset = braintrust.init_dataset(
                project=project,
                name=dataset,
                api_key=api_key,
            )
        bt_experiment: Any = braintrust.init(
            project=project,
            experiment=experiment,
            api_key=api_key,
            dataset=bt_dataset,
        )
        for row in rows:
            log_kwargs: dict[str, Any] = {
                "input": row.input,
                "output": row.output,
                "expected": row.expected,
                "scores": row.scores,
                "metadata": row.metadata,
            }
            case_id = row.metadata.get("case_id") or row.input.get("case_id")
            if dataset is not None and case_id:
                log_kwargs["dataset_record_id"] = case_id
            bt_experiment.log(**log_kwargs)
        bt_experiment.flush()
    except Exception:
        log.exception("braintrust experiment push failed; results not uploaded")
        return 0
    return len(rows)


def _reset_cache_for_tests() -> None:  # pyright: ignore[reportUnusedFunction]
    """Test hook — drop the cached logger so a fresh ``get()`` re-resolves."""
    global _logger_cache
    _logger_cache = None
