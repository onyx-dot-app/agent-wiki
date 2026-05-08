"""Three Huey queues, one SQLite file.

We deliberately split background work into **three independent queues**, each
with its own ``SqliteHuey`` instance and its own consumer process. They share
``queue.sqlite`` (Huey namespaces tables by ``name=``), so the operational
story stays simple — one volume, one backup target — but each queue's backlog
is isolated from the others. A slow LLM doc-rewrite can't delay an FTS
reindex; a flood of trigger evaluations can't backpressure connector ingest.

Each constant below is the canonical entry point for tasks that belong on
that queue. Don't add a fourth queue without a real reason — the value of
this split comes from the queues being a small, fixed set that the rest of
the system can reason about.

Queues:

* ``documents_huey`` — **LLM doc-reconciliation work.** Slow, expensive,
  LLM-bound. Anything that runs the document-updater agent against a wiki
  page goes here. Tasks make full LLM calls and may produce a new commit;
  we keep them off the indexer/triggers paths so a slow Anthropic call
  can't backpressure FTS reindex or trigger fan-out.
  Today: connector ingest (``update_document_from_payload``), direct agent
  edits (``agent_update_document_nl``), and the periodic stale-doc review
  (``stale_doc_review``).

* ``triggers_huey`` — **natural-language trigger evaluation (delta +
  scheduled).** All trigger evaluation, both event-driven and time-based.
  Post-commit fan-out (``fan_out_trigger_eval``) runs the SQL match against
  ``doc_path`` plus every parent directory and dispatches one or two LLM
  calls per matched trigger. The 5-min cron ``evaluate_scheduled_triggers``
  (kind=schedule triggers due now) lives here too — same evaluator, same
  code paths, different ignition. Kept separate from ``documents_huey``
  because trigger eval is read-only (no commits) and we want one queue's
  backlog to be the only thing that delays an event-log entry.

* ``wiki_bm25_huey`` — **FTS5 / BM25 indexer.** Cheap, frequent, no
  LLM. Re-indexes a single wiki path into the ``documents_fts`` table from
  the current git working tree. Runs after every successful ``commit_file``
  (whether human edit, agent edit, move, or doc-updater commit) and on
  demand from ``POST /api/documents/reindex``. On its own queue so search
  staleness is bounded by indexer throughput alone — never blocked behind
  a multi-second LLM call.

Each consumer runs as a separate worker container — see
``docker-compose.yml`` (``worker-documents``, ``worker-triggers``,
``worker-wiki-bm25``) and ``app/tasks/run_worker.py``.
"""
from __future__ import annotations

from huey import SqliteHuey

from app.config import CONFIG


class QueueFullError(RuntimeError):
    """Raised when a producer tries to enqueue past the configured cap.

    Each queue has a hard size limit (``MAX_QUEUE_SIZE``, default 1000) so
    a runaway producer can't fill the SQLite queue file or starve the
    consumer with a backlog it will never catch up on. Callers (mostly API
    routes that enqueue) translate this to a 503 with a clear message.
    """

    def __init__(self, queue_name: str, size: int, limit: int) -> None:
        super().__init__(
            f"queue '{queue_name}' is full: {size} pending tasks at the configured "
            f"limit of {limit} (MAX_QUEUE_SIZE). Try again after the worker drains."
        )
        self.queue_name = queue_name
        self.size = size
        self.limit = limit


class BoundedSqliteHuey(SqliteHuey):
    """SqliteHuey that rejects enqueue when the backlog is at the limit.

    Huey itself has no built-in bound; we check ``storage.queue_size()``
    just before handing the message to storage. The check is racy under
    concurrent producers (no transactional guard), but the cap is a
    coarse safeguard against runaway producers, not a fairness mechanism.
    """

    def __init__(self, *args, max_size: int, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._max_queue_size = max_size

    @property
    def max_queue_size(self) -> int:
        return self._max_queue_size

    def enqueue(self, task):
        # ``immediate`` mode (used in tests) doesn't touch storage — skip the
        # cap so test fixtures don't need to worry about queue size.
        if not self._immediate:
            size = self.storage.queue_size()
            if size >= self._max_queue_size:
                raise QueueFullError(self.name, size, self._max_queue_size)
        return super().enqueue(task)


def _make(name: str) -> BoundedSqliteHuey:
    return BoundedSqliteHuey(
        name=name,
        filename=CONFIG.queue_db_path,
        max_size=CONFIG.max_queue_size,
    )


documents_huey = _make("documents")
triggers_huey = _make("triggers")
wiki_bm25_huey = _make("wiki_bm25")

# Map queue-name → instance, used by run_worker.py to launch the right
# consumer per worker container.
QUEUES = {
    "documents": documents_huey,
    "triggers": triggers_huey,
    "wiki_bm25": wiki_bm25_huey,
}
