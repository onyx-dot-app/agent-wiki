"""Three task queues, all backed by pgmq on Postgres.

We deliberately split background work into **three independent queues**, each
with its own ``TaskQueue`` instance and its own consumer process. Each backs
its messages to a pgmq queue (``pgmq.q_<name>``) inside the same Postgres
database that holds app state, so the operational story stays simple — one
DB, one backup target — but each queue's backlog is isolated from the
others. A slow LLM doc-rewrite can't delay a BM25 reindex; a flood of
trigger evaluations can't backpressure connector ingest.

Each constant below is the canonical entry point for tasks that belong on
that queue. Don't add a fourth queue without a real reason — the value of
this split comes from the queues being a small, fixed set that the rest of
the system can reason about.

Queues:

* ``documents_queue`` — **LLM doc-reconciliation work.** Slow, expensive,
  LLM-bound. Anything that runs the document-updater agent against a wiki
  page goes here. Tasks make full LLM calls and may produce a new commit;
  we keep them off the indexer/triggers paths so a slow Anthropic call
  can't backpressure BM25 reindex or trigger fan-out.
  Today: connector ingest (``update_document_from_payload``) and direct
  agent edits (``agent_update_document_nl``).

* ``triggers_queue`` — **natural-language trigger evaluation (delta +
  scheduled).** All trigger evaluation, both event-driven and time-based.
  Post-commit fan-out (``fan_out_trigger_eval``) runs the SQL match against
  ``doc_path`` plus every parent directory and dispatches one or two LLM
  calls per matched trigger. The 5-min cron ``evaluate_scheduled_triggers``
  (kind=schedule triggers due now) lives here too — same evaluator, same
  code paths, different ignition. Kept separate from ``documents_queue``
  because trigger eval is read-only (no commits) and we want one queue's
  backlog to be the only thing that delays an event-log entry.

* ``wiki_bm25_queue`` — **BM25 indexer.** Cheap, frequent, no LLM.
  Re-indexes a single wiki path into the ``documents_fts`` table from the
  current git working tree. Runs after every successful ``commit_file``
  (whether human edit, agent edit, move, or doc-updater commit) and on
  demand from ``POST /api/documents/reindex``. On its own queue so search
  staleness is bounded by indexer throughput alone — never blocked behind
  a multi-second LLM call.

Each consumer runs as a separate worker container — see
``docker-compose.yml`` (``worker-documents``, ``worker-triggers``,
``worker-wiki-bm25``) and ``app/tasks/run_worker.py``.
"""
from __future__ import annotations

from app.config import CONFIG
from app.tasks.queue import QueueFullError, TaskQueue

__all__ = [
    "QueueFullError",
    "QUEUES",
    "documents_queue",
    "triggers_queue",
    "wiki_bm25_queue",
]


def _make(name: str) -> TaskQueue:
    return TaskQueue(name=name, max_size=CONFIG.max_queue_size)


documents_queue = _make("documents")
triggers_queue = _make("triggers")
wiki_bm25_queue = _make("wiki_bm25")

# Map queue-name → instance, used by run_worker.py to launch the right
# consumer per worker container.
QUEUES = {
    "documents": documents_queue,
    "triggers": triggers_queue,
    "wiki_bm25": wiki_bm25_queue,
}
