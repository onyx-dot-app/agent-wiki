"""Prometheus metrics for agent-wiki.

All metric objects are module-level singletons so they're registered once
at import time. Import this module early (before workers fork) to ensure
consistent registration across processes.

HTTP request metrics (rate, latency, errors) are added automatically by
``setup_prometheus`` via prometheus-fastapi-instrumentator.

Ingest pipeline metrics must be updated manually at each pipeline stage.
"""
from __future__ import annotations

import logging

from fastapi import FastAPI
from prometheus_client import Counter, Gauge, Histogram, REGISTRY
from prometheus_client.core import GaugeMetricFamily  # type: ignore[import-untyped]
from prometheus_fastapi_instrumentator import Instrumentator

from app.db import fts

log = logging.getLogger(__name__)

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
    # committed, no_change, irrelevant, no_candidates, filtered (source type),
    # filtered_by_bm25_score (below BM25 threshold), filtered_by_selector,
    # ingestion_auto_update_disabled
    ["outcome", "wiki_path"],
)

ingest_document_results_total = Counter(
    "ingest_document_results_total",
    "Per-document terminal outcome of ingest reconciliation, one increment per "
    "document. A document is 'committed' if any candidate page was committed, "
    "else 'no_change' if any candidate resolved to no-change, else 'irrelevant'. "
    "'no_candidates' is recorded when search yields no usable candidate page "
    "(no BM25 hit above threshold, or none readable), so this metric accounts "
    "for every ingested document, not just those that reached reconciliation.",
    ["result"],  # committed, no_change, irrelevant, no_candidates
)

ingest_document_chars = Histogram(
    "ingest_document_chars",
    "Size of the incoming document in characters, by source type",
    ["source_type"],
    buckets=[500, 1_000, 2_000, 5_000, 10_000, 20_000, 50_000, 100_000, 200_000],
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

class _WikiPagesCollector:
    def collect(self):
        count = fts.count_documents() or 0
        g = GaugeMetricFamily(
            "wiki_pages_total",
            "Total number of wiki pages currently in the search index",
        )
        g.add_metric([], count)
        yield g

REGISTRY.register(_WikiPagesCollector())


class _WikiAutoUpdateCollector:
    def collect(self):
        from app.wiki import update_policy

        total = fts.count_documents() or 0
        try:
            enabled = update_policy.count_ingest_enabled_pages(total, fts.paths_under)
        except Exception:
            log.warning("metrics: auto-update-enabled count failed", exc_info=True)
            enabled = total
        g = GaugeMetricFamily(
            "wiki_pages_auto_update_enabled",
            "Wiki pages with ingestion auto-update enabled (effective policy)",
        )
        g.add_metric([], enabled)
        yield g

REGISTRY.register(_WikiAutoUpdateCollector())


class _TaskQueueCollector:
    """Per-queue backlog depth + oldest-message age, sampled at scrape time
    (mirrors the queue set in ``app/tasks/queues.py``). Depth catches a *growing*
    backlog; oldest-age catches a *stalled* queue — a message sitting unworked
    behind a slow/stuck worker — which depth alone misses when the backlog is
    small. This is the signal that surfaced the 2026-07 checkpoint starvation."""

    def collect(self):
        from app.tasks.queues import QUEUES

        depth = GaugeMetricFamily(
            "task_queue_depth",
            "Pending messages (ready + delayed) per task queue",
            labels=["queue"],
        )
        age = GaugeMetricFamily(
            "task_queue_oldest_age_seconds",
            "Age of the oldest ready message per task queue (0 when empty)",
            labels=["queue"],
        )
        for name, q in QUEUES.items():
            try:
                depth.add_metric([name], q.depth().pending)
                age.add_metric([name], q.oldest_age_seconds() or 0.0)
            except Exception:
                log.warning("metrics: queue stats failed for %s", name, exc_info=True)
        yield depth
        yield age


REGISTRY.register(_TaskQueueCollector())

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

ingest_batch_reconciler_duration_seconds = Histogram(
    "ingest_batch_reconciler_duration_seconds",
    "Total time spent on batch reconciler LLM call(s) per ingest request",
    buckets=[0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0],
)

_TOKEN_BUCKETS = [128, 256, 512, 1024, 2048, 4096, 8192, 16384]

ingest_selector_input_tokens = Histogram(
    "ingest_selector_input_tokens",
    "Input tokens per selector batch call (provider-raw; _count drives call counts)",
    buckets=_TOKEN_BUCKETS,
)

# Provider-consistent prompt-cache split of selector input tokens: cached =
# served from the prompt cache, uncached = processed fresh. Sum ≈ total input.
ingest_selector_cached_input_tokens = Histogram(
    "ingest_selector_cached_input_tokens",
    "Cached (prompt-cache hit) input tokens per selector batch call",
    buckets=_TOKEN_BUCKETS,
)

ingest_selector_uncached_input_tokens = Histogram(
    "ingest_selector_uncached_input_tokens",
    "Uncached (freshly processed) input tokens per selector batch call",
    buckets=_TOKEN_BUCKETS,
)

ingest_selector_output_tokens = Histogram(
    "ingest_selector_output_tokens",
    "Output tokens per selector batch call",
    buckets=_TOKEN_BUCKETS,
)

ingest_reconciler_input_tokens = Histogram(
    "ingest_reconciler_input_tokens",
    "Input tokens per reconciler batch call (provider-raw; _count drives call counts)",
    buckets=_TOKEN_BUCKETS,
)

ingest_reconciler_cached_input_tokens = Histogram(
    "ingest_reconciler_cached_input_tokens",
    "Cached (prompt-cache hit) input tokens per reconciler batch call",
    buckets=_TOKEN_BUCKETS,
)

ingest_reconciler_uncached_input_tokens = Histogram(
    "ingest_reconciler_uncached_input_tokens",
    "Uncached (freshly processed) input tokens per reconciler batch call",
    buckets=_TOKEN_BUCKETS,
)

ingest_reconciler_output_tokens = Histogram(
    "ingest_reconciler_output_tokens",
    "Output tokens per reconciler batch call",
    buckets=_TOKEN_BUCKETS,
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
