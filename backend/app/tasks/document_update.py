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
