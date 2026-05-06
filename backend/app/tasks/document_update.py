"""Background task: run the LLM agent to reconcile a doc with new info.

This is the core "wikis stay current" loop. v0 hands the agent a single doc
and the new payload; later versions can scale this with batching, dedup, etc.
Watch the cost — every connector update triggering a full LLM pass is
expensive.
"""
from __future__ import annotations

from app.tasks.huey_app import huey


@huey.task()
def update_document_from_payload(doc_id: str, source: str, payload: dict) -> None:
    # TODO:
    #   1. Load current doc body from git (app.wiki.git.read_file).
    #   2. Call app.llm.agents.document_updater.run(doc_id, body, payload, source).
    #   3. If the agent produced a new body, commit it (app.wiki.git.commit_file).
    #   4. Enqueue reindex_document.
    #   5. Run trigger evaluation against the doc + parent dirs.
    raise NotImplementedError


@huey.task()
def update_document_direct(doc_id: str, new_body: str, message: str, author: str) -> None:
    # Used when an agent edits a doc through the API rather than from a payload.
    raise NotImplementedError
