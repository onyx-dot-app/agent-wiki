"""LLM doc-reconciliation tasks — the "wikis stay current" loop.

Tasks in this module run on the ``documents_huey`` queue — the slow,
LLM-bound queue dedicated to running the document-updater agent against a
wiki page. Each task may make a full LLM call and produce a new commit, so
we keep this work off the indexer / trigger queues to prevent provider
slowness from cascading into search staleness or delayed trigger fires.

After a successful commit, these tasks re-enqueue ``reindex_path`` (on
``wiki_bm25_huey``) and ``fan_out_trigger_eval`` (on
``triggers_huey``) so the side effects fan out exactly like a human edit.

v0 hands the agent a single doc and the new payload; later versions can
scale this with batching, dedup, etc. Watch the cost — every connector
update triggering a full LLM pass is expensive.

See ``app/tasks/huey_app.py`` for the queue rationale.
"""
from __future__ import annotations

import logging

from app.tasks.huey_app import documents_huey

log = logging.getLogger(__name__)


@documents_huey.task()
def update_document_from_payload(doc_id: str, source: str, payload: dict) -> None:
    log.info("update_document_from_payload doc_id=%s source=%s", doc_id, source)
    # TODO:
    #   1. Load current doc body from git (app.wiki.git.read_file).
    #   2. Call app.llm.agents.document_updater.run(doc_id, body, payload, source).
    #   3. If the agent produced a new body, commit it (app.wiki.git.commit_file).
    #   4. Enqueue reindex_path on wiki_bm25_huey.
    #   5. Enqueue fan_out_trigger_eval on triggers_huey for doc + parent dirs.
    raise NotImplementedError


@documents_huey.task()
def agent_update_document_nl(doc_id: str, new_body: str, message: str, author: str) -> None:
    # Used when an agent edits a doc through the API rather than from a payload.
    log.info("agent_update_document_nl doc_id=%s author=%s", doc_id, author)
    raise NotImplementedError


@documents_huey.task()
def process_pushed_document(push: dict) -> None:
    """Reconcile a document pushed from an external system into the wiki.

    ``push`` is the validated payload from POST /api/documents/ingest. Shape:
    ``{content, title?, source_type?, metadata?, updated_at?, diff?}``. The
    document-updater agent decides which wiki page(s) to update based on
    these fields; the API layer does no routing.
    """
    log.info(
        "process_pushed_document source_type=%s title=%s len=%d",
        push.get("source_type"), push.get("title"), len(push.get("content") or ""),
    )
    # TODO:
    #   1. Resolve target wiki page(s) (LLM-routed via document-updater agent).
    #   2. Run document-updater against the resolved page with the push.
    #   3. On a body change, commit + reindex + trigger fan-out (mirrors the
    #      update_document_from_payload pipeline above).
    raise NotImplementedError
