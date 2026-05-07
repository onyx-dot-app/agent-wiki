"""Document APIs.

Three callers we care about:
  * Humans (browse, read, edit) — addressed by ``path``.
  * Agents updating a doc — addressed by ``doc_id``; updates are git-committed.
  * Connectors pushing generic updates that need to be reconciled by the
    LLM agent — these are queued for background processing.
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request

from app.auth import current_user, login_required
from app.tasks.reindex import reindex_path
from app.tasks.triggers import fan_out_trigger_eval
from app.wiki import filesystem, git as wiki_git

bp = Blueprint("documents", __name__)


@bp.get("")
@login_required
def list_documents():
    prefix = request.args.get("prefix", "")
    paths = wiki_git.list_paths(prefix)
    return jsonify(paths=paths)


@bp.get("/file")
@login_required
def get_document_by_path():
    path = request.args.get("path", "")
    if not path:
        return jsonify(error="path required"), 400
    try:
        rel = filesystem.safe_rel_path(path)
    except ValueError as e:
        return jsonify(error=str(e)), 400
    abs_path = filesystem.absolute(rel)
    if not abs_path.is_file():
        return jsonify(error="not found"), 404
    return jsonify(path=rel, body=abs_path.read_text())


@bp.put("/file")
@login_required
def put_document_by_path():
    data = request.get_json(silent=True) or {}
    path = data.get("path", "")
    body = data.get("body", "")
    if not path:
        return jsonify(error="path required"), 400
    try:
        rel = filesystem.safe_rel_path(path)
    except ValueError as e:
        return jsonify(error=str(e)), 400
    if not rel.endswith(".md"):
        return jsonify(error="only .md files are supported"), 400
    abs_path = filesystem.absolute(rel)
    existed = abs_path.is_file()
    user = current_user()
    author = f"{user.name or user.email} <{user.email}>" if user else None
    change_kind = "edit" if existed else "create"
    msg = f"{change_kind} {rel}"
    sha = wiki_git.commit_file(rel, body, msg, author=author)
    reindex_path(rel)
    fan_out_trigger_eval(rel, sha, change_kind, author)
    return jsonify(path=rel, sha=sha, created=not existed)


@bp.delete("/file")
@login_required
def delete_document_by_path():
    path = request.args.get("path", "")
    if not path:
        return jsonify(error="path required"), 400
    try:
        rel = filesystem.safe_rel_path(path)
    except ValueError as e:
        return jsonify(error=str(e)), 400
    abs_path = filesystem.absolute(rel)
    if not abs_path.exists():
        return jsonify(error="not found"), 404
    user = current_user()
    author = f"{user.name or user.email} <{user.email}>" if user else None
    sha = wiki_git.delete_path(rel, f"delete {rel}", author=author)
    return jsonify(sha=sha)


@bp.post("/reindex")
@login_required
def reindex_document_by_path():
    data = request.get_json(silent=True) or {}
    path = data.get("path", "")
    if not path:
        return jsonify(error="path required"), 400
    try:
        rel = filesystem.safe_rel_path(path)
    except ValueError as e:
        return jsonify(error=str(e)), 400
    abs_path = filesystem.absolute(rel)
    if not abs_path.is_file():
        return jsonify(error="not found"), 404
    reindex_path(rel)
    return jsonify(path=rel, queued=True)


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
