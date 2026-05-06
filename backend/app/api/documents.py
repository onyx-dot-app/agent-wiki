"""Document APIs.

Three callers we care about:
  * Humans (browse, read, edit) — addressed by ``path``.
  * Agents updating a doc — addressed by ``doc_id``; updates are git-committed.
  * Connectors pushing generic updates that need to be reconciled by the
    LLM agent — these are queued for background processing.
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request

from app.auth import login_required

bp = Blueprint("documents", __name__)


@bp.get("")
@login_required
def list_documents():
    # TODO: list docs with optional path prefix filter
    raise NotImplementedError


@bp.get("/search")
@login_required
def search_documents():
    # TODO: FTS5 query → ranked results (uses app.db.fts.search)
    raise NotImplementedError


@bp.get("/<doc_id>")
@login_required
def get_document(doc_id: str):
    # TODO: read from git working tree, return body + metadata
    raise NotImplementedError


@bp.put("/<doc_id>")
@login_required
def update_document(doc_id: str):
    # Used by agents directly editing a doc.
    # TODO: write file, git commit, enqueue reindex task, write event.
    raise NotImplementedError


@bp.post("/ingest")
@login_required
def ingest_update():
    # Generic connector update — passed to the LLM agent harness, which
    # decides which doc(s) to update.
    # body: {source, payload}
    # TODO: write event, enqueue document_update task.
    raise NotImplementedError


@bp.get("/<doc_id>/history")
@login_required
def document_history(doc_id: str):
    # TODO: shell out to `git log` for the doc path.
    raise NotImplementedError
