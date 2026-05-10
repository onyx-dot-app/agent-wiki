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

* ``lightweight_maintenance_queue`` — **fast upkeep tasks.** Sub-second,
  no LLM, no external HTTP, no wiki commits. Anything that fits that
  profile and isn't worth its own queue lives here. The placement rule
  matters: this queue runs wider concurrency (4 workers) on the
  assumption that handlers return quickly, so dropping a slow task in
  here would silently starve the others. If a new task can't honor the
  rule, give it its own queue rather than co-tenanting on this one.
  Today:
    - BM25 reindex of a single wiki path from the git working tree
      (``reindex_path``, ``reindex_document``) — runs after every
      ``commit_file`` and on demand from ``POST /api/documents/reindex``.
    - Agent-activity expiration cleanup
      (``cleanup_expired_activity``) — a single ``DELETE`` enqueued
      with a delay equal to the row's ``expires_at``.

Each consumer runs as a separate worker container — see
``docker-compose.yml`` (``worker-documents``, ``worker-triggers``,
``worker-lightweight-maintenance``) and ``app/tasks/run_worker.py``.
"""
from __future__ import annotations

from app.config import CONFIG
from app.tasks.queue import QueueFullError, TaskQueue

__all__ = [
    "QueueFullError",
    "QUEUES",
    "documents_queue",
    "lightweight_maintenance_queue",
    "triggers_queue",
]


def _make(name: str) -> TaskQueue:
    return TaskQueue(name=name, max_size=CONFIG.max_queue_size)


documents_queue = _make("documents")
triggers_queue = _make("triggers")
lightweight_maintenance_queue = _make("lightweight_maintenance")

# Map queue-name → instance, used by run_worker.py to launch the right
# consumer per worker container.
QUEUES = {
    "documents": documents_queue,
    "triggers": triggers_queue,
    "lightweight_maintenance": lightweight_maintenance_queue,
}
