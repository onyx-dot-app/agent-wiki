"""Five task queues backed by Redis Streams.

We deliberately split background work into **five independent queues**, each
with its own ``TaskQueue`` instance and its own consumer process. Each queue
is a Redis Stream (``queue:{name}``) so each queue's backlog is isolated from
the others. A slow LLM doc-rewrite can't delay a reindex; a flood of
trigger evaluations can't backpressure connector ingest; a co-edit checkpoint
can't sit behind an hour of connector ingest; a whole-space detection sweep
can't starve any of them.

Each constant below is the canonical entry point for tasks that belong on
that queue. Keep this a small, fixed set — the value of the split comes from
the queues being few and reason-about-able. Add one only when a workload's
latency/throughput profile genuinely conflicts with every existing queue.

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
  calls per matched trigger. The every-minute cron ``evaluate_scheduled_triggers``
  (kind=schedule triggers due now) lives here too — same evaluator, same
  code paths, different ignition. Kept separate from ``documents_queue``
  because trigger eval is read-only (no commits) and we want one queue's
  backlog to be the only thing that delays an event-log entry.
  Onyx Craft launches also ride this queue (each blocks ~10-60s on Onyx
  sandbox provisioning).

* ``coedit_queue`` — **co-edit session checkpoints.** Commits a live session
  buffer to git and, on a concurrent agent/ingest commit, runs an AI merge —
  so like ``documents_queue`` it commits and may hit the LLM, and can't live on
  ``lightweight_maintenance_queue``. It gets its *own* queue because a
  checkpoint's freshness is user-visible (readers see git HEAD): parked behind
  hours of connector ingest on ``documents_queue``, a session's committed page
  goes stale and, in the limit, pins readers to a lagging buffer. Runs wider
  than ``documents`` because per-session safety comes from
  a Postgres advisory lock (``coedit.checkpoint_lock_key``), not single-threading
  — different sessions checkpoint in parallel; the same session is serialized.

* ``automanage_queue`` — **Wiki Auto Management detection + execution.** A
  whole-space sweep walks the wiki, runs the detectors, and emits
  ``change_proposals`` (mechanical today; fuzzy-dup/misplacement will add LLM
  calls), and on approval the executor commits structural changes to git. That
  profile — long, bursty, eventually LLM-bound, and committing — conflicts with
  every other queue's contract: it would starve connector ingest on
  ``documents`` (concurrency 1), delay latency-sensitive fan-out on
  ``triggers``, sit in front of user-visible checkpoints on ``coedit``, and
  break ``lightweight_maintenance``'s no-LLM/no-commit rule. So it gets its own
  queue. Safety on concurrent executes comes from the git commit lock, like
  ``coedit`` — not single-threading.

* ``lightweight_maintenance_queue`` — **fast upkeep tasks.** Sub-second,
  no LLM, no external HTTP, no wiki commits. Anything that fits that
  profile and isn't worth its own queue lives here. The placement rule
  matters: this queue runs wider concurrency (4 workers) on the
  assumption that handlers return quickly, so dropping a slow task in
  here would silently starve the others. If a new task can't honor the
  rule, give it its own queue rather than co-tenanting on this one.
  Today:
    - Search index tasks (``index_path``) — BM25 via OpenSearch.
    - Agent-activity expiration cleanup
      (``cleanup_expired_activity``) — a single ``DELETE`` enqueued
      with a delay equal to the row's ``expires_at``.
    - ingest_eval_samples retention
      (``prune_ingest_eval_samples``) — a daily, per-run-bounded indexed
      ``DELETE`` of rows past ``INGEST_EVAL_RETENTION_DAYS``.

Each consumer runs as a separate worker container — see
``docker-compose.yml`` (``worker-documents``, ``worker-triggers``,
``worker-coedit``, ``worker-lightweight-maintenance``) and
``app/tasks/run_worker.py``.
"""
from __future__ import annotations

from app.config import CONFIG
from app.tasks.queue import QueueFullError, TaskQueue

__all__ = [
    "QueueFullError",
    "QUEUES",
    "automanage_queue",
    "coedit_queue",
    "documents_queue",
    "lightweight_maintenance_queue",
    "triggers_queue",
]


def _make(name: str) -> TaskQueue:
    return TaskQueue(name=name, max_size=CONFIG.max_queue_size)


documents_queue = _make("documents")
triggers_queue = _make("triggers")
coedit_queue = _make("coedit")
automanage_queue = _make("automanage")
lightweight_maintenance_queue = _make("lightweight_maintenance")

# Map queue-name → instance, used by run_worker.py to launch the right
# consumer per worker container.
QUEUES = {
    "documents": documents_queue,
    "triggers": triggers_queue,
    "coedit": coedit_queue,
    "automanage": automanage_queue,
    "lightweight_maintenance": lightweight_maintenance_queue,
}
