"""Prometheus metrics for agent-wiki.

All metric objects are module-level singletons so they're registered once
at import time. Import this module early (before workers fork) to ensure
consistent registration across processes.

HTTP request metrics (rate, latency, errors) are added automatically by
``setup_prometheus`` via prometheus-fastapi-instrumentator.

Ingest pipeline metrics must be updated manually at each pipeline stage.
"""
from __future__ import annotations

from fastapi import FastAPI
from prometheus_client import Counter, Gauge, Histogram
from prometheus_fastapi_instrumentator import Instrumentator

# --------------------------------------------------------------------------- #
# Ingest pipeline                                                              #
# --------------------------------------------------------------------------- #

ingest_requests_total = Counter(
    "ingest_requests_total",
    "Total ingest requests received",
    ["source_type"],
)

ingest_bm25_hits = Histogram(
    "ingest_bm25_hits",
    "Number of BM25 hits returned per ingest request",
    buckets=[0, 1, 2, 5, 10, 20],
)

ingest_bm25_passed = Histogram(
    "ingest_bm25_passed",
    "Number of BM25 candidates passing threshold per ingest request",
    buckets=[0, 1, 2, 5, 10, 20],
)

ingest_bm25_score = Histogram(
    "ingest_bm25_score",
    "Raw BM25 score for each candidate before threshold filtering",
    buckets=[1.0, 5.0, 10.0, 20.0, 50.0, 100.0, 200.0, 300.0, 500.0, 750.0, 1000.0, 1500.0],
)

ingest_bm25_score_by_outcome = Histogram(
    "ingest_bm25_score_by_outcome",
    "BM25 score distribution broken down by ingest outcome",
    ["outcome"],
    buckets=[1.0, 5.0, 10.0, 20.0, 50.0, 100.0, 200.0, 300.0, 500.0, 750.0, 1000.0, 1500.0],
)

ingest_outcomes_total = Counter(
    "ingest_outcomes_total",
    "Ingest pipeline outcomes",
    # wiki_path cardinality is bounded by the number of pages in the wiki git repo
    # (a finite set). Stale series from renamed/deleted pages expire with TSDB retention.
    # Add a recording rule to cap top-N if the wiki grows beyond ~500 pages.
    ["outcome", "wiki_path"],  # committed, no_change, irrelevant, filtered, no_candidates
)

ingest_llm_duration_seconds = Histogram(
    "ingest_llm_duration_seconds",
    "LLM call duration per wiki page reconciliation",
    buckets=[0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0],
)

ingest_llm_calls_per_doc = Histogram(
    "ingest_llm_calls_per_doc",
    "Number of LLM calls made per document ingestion",
    buckets=[0, 1, 2, 3, 5, 8, 10, 15, 20],
)

ingest_queue_depth = Gauge(
    "ingest_queue_depth",
    "Current number of pending tasks in the documents queue",
)

ingest_selector_candidates_filtered = Histogram(
    "ingest_selector_candidates_filtered",
    "Candidates dropped by the weak-model selector per ingest request",
    buckets=[0, 1, 2, 3, 5, 8, 10, 15, 20],
)

ingest_selector_calls_per_doc = Histogram(
    "ingest_selector_calls_per_doc",
    "Number of selector LLM calls (batches) made per document ingestion",
    buckets=[0, 1, 2, 3, 5, 8, 10, 15, 20],
)

ingest_selector_duration_seconds = Histogram(
    "ingest_selector_duration_seconds",
    "Total time spent on selector LLM call(s) per ingest request",
    buckets=[0.5, 1.0, 2.0, 5.0, 10.0, 30.0],
)


# --------------------------------------------------------------------------- #
# HTTP instrumentation                                                         #
# --------------------------------------------------------------------------- #

_LATENCY_BUCKETS = (0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)

_EXCLUDED_HANDLERS = ["/api/health", "/metrics"]

_prometheus_initialized = False


def setup_prometheus(app: FastAPI) -> None:
    """Wire Prometheus HTTP instrumentation into the FastAPI app.

    Must be called in ``create_app()`` before the app starts serving.
    Exposes ``GET /metrics`` for Prometheus scraping.

    The guard prevents duplicate metric registration when ``create_app()``
    is called multiple times in the same process (e.g. per-test fixtures).
    """
    global _prometheus_initialized
    if _prometheus_initialized:
        return
    Instrumentator(
        should_group_status_codes=False,
        should_ignore_untemplated=False,
        should_group_untemplated=True,
        should_instrument_requests_inprogress=True,
        excluded_handlers=_EXCLUDED_HANDLERS,
    ).instrument(app, latency_lowr_buckets=_LATENCY_BUCKETS).expose(app)
    _prometheus_initialized = True
