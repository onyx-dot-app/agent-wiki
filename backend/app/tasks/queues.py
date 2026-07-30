"""Six task queues backed by Redis Streams.

We deliberately split background work into **six independent queues**, each
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

* ``coedit_queue`` — **co-edit checkpointing + session leave fallback.** A
  session's live Yjs document only exists as one *web* process's in-memory
  document, rebuilt on demand from ``(ydoc_snapshot, coedit_updates)`` (see
  ``app/wiki/coedit_live.py``), so any worker can act on any session, and
  be shared cross-process), but checkpointing doesn't need that room: the
  checkpoint engine (``app/wiki/coedit_checkpoint.py``) rebuilds its own
  throwaway ``Doc`` from the session's persisted snapshot + update log, so
  ``checkpoint_coedit_session_task`` (``app/tasks/coedit_checkpoint.py``) is
  a real task any worker can dequeue and act on, regardless of which process
  (if any) holds the session's room live — a periodic scan enqueues one per
  dirty session, explicit save and last-participant-leave enqueue directly.
  A checkpoint's result is fanned out over the realtime bus so any process
  that *does* hold the session's room live can reconcile it. This queue also
  still carries ``leave_coedit_session``, the durable fallback the WS route
  enqueues when its own connection-teardown task is cancelled before it can
  record a leave itself (server shutdown, a torn-down test connection) — a
  plain Redis send nothing can cancel. See ``app/tasks/coedit_leave.py``.

Wiki Auto Management splits by **latency tier** — whether anyone is waiting on
the result (see the "Queues and Workers" design doc):

* ``automanage_offline_queue`` — **offline: batch, unattended.** Whole-space
  sweeps (on-demand + scheduled) that walk the wiki, run the detectors, and emit
  ``change_proposals`` (mechanical today; fuzzy-dup/misplacement will add LLM
  calls), plus the AI-managed auto-apply executes the sweep fans out. Nobody
  waits: long, bursty, eventually LLM-bound, and (for auto-applies) committing —
  a profile that conflicts with every other queue's contract (it would starve
  connector ingest on ``documents``, delay ``triggers`` fan-out, sit in front of
  ``coedit`` checkpoints, break ``lightweight_maintenance``'s no-LLM/no-commit
  rule), so it stays isolated.

* ``automanage_nearline_queue`` — **nearline: a human is waiting.** Execution of
  a *human-approved* proposal. Kept off the offline queue so an approval applies
  promptly instead of head-of-line-blocking behind an in-flight sweep or a batch
  of AI auto-applies. Still async — a future execution op may be LLM-bound, so
  it never runs inline in the approve request. Concurrency safety on both
  automanage queues comes from the git commit lock, like ``coedit``.

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

Queues are the *isolation* unit (each its own Redis stream + consumer/thread
pool); worker **processes** are the *memory* unit and host several queues each.
Deployment groups them into two pods by blast risk — ``worker-heavy``
(``documents``, ``triggers``, ``automanage_offline`` — the LLM-bound work,
quarantined so an OOM can't take down the rest) and ``worker-light``
(``coedit``, ``automanage_nearline``, ``lightweight_maintenance`` — real-time +
interactive + fast). See ``docker-compose.yml``, the ``worker.groups`` values in
the helm chart, ``app/tasks/run_worker.py``, and the "Queues and Workers" design
doc.
"""
from __future__ import annotations

from app.config import CONFIG
from app.tasks.queue import QueueFullError, TaskQueue

__all__ = [
    "QueueFullError",
    "QUEUES",
    "automanage_nearline_queue",
    "automanage_offline_queue",
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
automanage_offline_queue = _make("automanage_offline")
automanage_nearline_queue = _make("automanage_nearline")
lightweight_maintenance_queue = _make("lightweight_maintenance")

# Map queue-name → instance, used by run_worker.py to launch the right
# consumer per worker container.
QUEUES = {
    "documents": documents_queue,
    "triggers": triggers_queue,
    "coedit": coedit_queue,
    "automanage_offline": automanage_offline_queue,
    "automanage_nearline": automanage_nearline_queue,
    "lightweight_maintenance": lightweight_maintenance_queue,
}
