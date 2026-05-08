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

documents_huey = SqliteHuey(name="documents", filename=CONFIG.queue_db_path)
triggers_huey = SqliteHuey(name="triggers", filename=CONFIG.queue_db_path)
wiki_bm25_huey = SqliteHuey(name="wiki_bm25", filename=CONFIG.queue_db_path)

# Map queue-name → instance, used by run_worker.py to launch the right
# consumer per worker container.
QUEUES = {
    "documents": documents_huey,
    "triggers": triggers_huey,
    "wiki_bm25": wiki_bm25_huey,
}
