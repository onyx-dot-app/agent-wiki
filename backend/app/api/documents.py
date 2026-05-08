"""Document APIs.

Three callers we care about:
  * Humans (browse, read, edit) — addressed by ``path``.
  * Agents updating a doc — addressed by ``doc_id``; updates are git-committed.
  * Connectors pushing generic updates that need to be reconciled by the
    LLM agent — these are queued for background processing.
"""
from __future__ import annotations

import logging
import re
import subprocess

from flask import Blueprint, jsonify, request

from app.auth import current_user, login_required
from app.tasks.reindex import reindex_path
from app.wiki import notify as wiki_notify
from app.triggers import repo as triggers_repo
from app.wiki import filesystem, git as wiki_git

bp = Blueprint("documents", __name__)
log = logging.getLogger(__name__)

# A rollback-edit records the SHAs it supersedes in a "Deprecates:" trailer in
# the new commit body. The history endpoint hides any sha listed in any later
# commit's trailer, so rolled-back-over revisions disappear without rewriting
# git history.
_DEPRECATES_RE = re.compile(r"^Deprecates:\s*(.+)$", re.MULTILINE)


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
    ref = request.args.get("ref")
    if not path:
        return jsonify(error="path required"), 400
    try:
        rel = filesystem.safe_rel_path(path)
    except ValueError as e:
        return jsonify(error=str(e)), 400
    head_sha = wiki_git.head_sha_for_path(rel)
    if ref:
        try:
            body = wiki_git.read_file(rel, ref=ref)
        except subprocess.CalledProcessError:
            return jsonify(error="not found at ref"), 404
        return jsonify(path=rel, body=body, ref=ref, head_sha=head_sha)
    abs_path = filesystem.absolute(rel)
    if not abs_path.is_file():
        return jsonify(error="not found"), 404
    return jsonify(path=rel, body=abs_path.read_text(), head_sha=head_sha)


@bp.put("/file")
@login_required
def put_document_by_path():
    data = request.get_json(silent=True) or {}
    path = data.get("path", "")
    body = data.get("body", "")
    base_sha = data.get("base_sha")
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
    deprecated: list[str] = []
    if base_sha:
        head = wiki_git.head_sha_for_path(rel)
        if head and head != base_sha:
            deprecated = wiki_git.commits_between(base_sha, head, rel)
    if deprecated:
        msg = f"{msg}\n\nDeprecates: {' '.join(deprecated)}"
    sha = wiki_git.commit_file(rel, body, msg, author=author)
    wiki_notify.after_doc_write(rel, sha, change_kind, author)
    log.info("doc %s %s by %s sha=%s", change_kind, rel, author or "?", sha[:8])
    return jsonify(path=rel, sha=sha, created=not existed, deprecated=deprecated)


@bp.post("/folder")
@login_required
def create_folder():
    """Create an (empty) wiki folder.

    Git doesn't track empty directories, so we drop a tiny `.gitkeep` marker
    inside. The explorer hides dotfiles, so the folder appears empty in the UI.
    """
    data = request.get_json(silent=True) or {}
    path = (data.get("path") or "").strip().strip("/")
    if not path:
        return jsonify(error="path required"), 400
    try:
        rel = filesystem.safe_rel_path(path)
    except ValueError as e:
        return jsonify(error=str(e)), 400
    if rel.endswith(".md"):
        return jsonify(error="folder path must not end in .md"), 400
    abs_path = filesystem.absolute(rel)
    if abs_path.is_file():
        return jsonify(error="a file with that path already exists"), 409
    if abs_path.is_dir():
        return jsonify(error="folder already exists"), 409
    user = current_user()
    author = f"{user.name or user.email} <{user.email}>" if user else None
    sha = wiki_git.commit_file(f"{rel}/.gitkeep", "", f"create folder {rel}", author=author)
    log.info("folder created %s by %s sha=%s", rel, author or "?", sha[:8])
    return jsonify(path=rel, sha=sha), 201


@bp.post("/move")
@login_required
def move_document_or_folder():
    """Rename or relocate a document or folder, single git commit.

    Used by the explorer's drag-and-drop and rename actions. Conflicts (the
    destination already exists) return 409 — the UI surfaces that as an error
    and leaves the source untouched.
    """
    data = request.get_json(silent=True) or {}
    old_raw = (data.get("old_path") or "").strip().strip("/")
    new_raw = (data.get("new_path") or "").strip().strip("/")
    if not old_raw or not new_raw:
        return jsonify(error="old_path and new_path required"), 400
    try:
        old_rel = filesystem.safe_rel_path(old_raw)
        new_rel = filesystem.safe_rel_path(new_raw)
    except ValueError as e:
        return jsonify(error=str(e)), 400
    if old_rel == new_rel:
        return jsonify(error="old_path and new_path are identical"), 400
    old_abs = filesystem.absolute(old_rel)
    new_abs = filesystem.absolute(new_rel)
    if not old_abs.exists():
        return jsonify(error="not found"), 404
    if new_abs.exists():
        return jsonify(error="a file or folder with that name already exists"), 409
    if old_abs.is_file() and old_rel.endswith(".md") and not new_rel.endswith(".md"):
        return jsonify(error="renaming a .md file requires the new name to end in .md"), 400
    if old_abs.is_dir() and new_rel.endswith(".md"):
        return jsonify(error="folder name must not end in .md"), 400

    user = current_user()
    author = f"{user.name or user.email} <{user.email}>" if user else None
    msg = f"move {old_rel} -> {new_rel}"
    try:
        sha, moves = wiki_git.move_path(old_rel, new_rel, msg, author=author)
    except subprocess.CalledProcessError as exc:
        log.warning("move_path git error %s -> %s: %s", old_rel, new_rel, exc.stderr)
        return jsonify(error="git move failed"), 500

    wiki_notify.after_path_move(moves, sha, author)

    # Trigger YAML files may have moved with their containing folder. The
    # SQLite cache stores their absolute file_path, so reconverge it from
    # disk. Cheap relative to the size of typical trigger sets.
    try:
        triggers_repo.rebuild_from_filesystem()
    except Exception:
        log.exception("trigger cache rebuild after move %s -> %s failed", old_rel, new_rel)

    log.info("move %s -> %s by %s sha=%s files=%d", old_rel, new_rel, author or "?", sha[:8], len(moves))
    return jsonify(
        old_path=old_rel,
        new_path=new_rel,
        sha=sha,
        moved=[{"old": o, "new": n} for o, n in moves],
    )


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
    wiki_notify.after_doc_delete(rel, sha, author)
    log.info("doc deleted %s by %s sha=%s", rel, author or "?", sha[:8])
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


@bp.get("/file/history")
@login_required
def file_history():
    path = request.args.get("path", "")
    if not path:
        return jsonify(error="path required"), 400
    try:
        rel = filesystem.safe_rel_path(path)
    except ValueError as e:
        return jsonify(error=str(e)), 400
    rows = wiki_git.history(rel)
    deprecated: set[str] = set()
    for r in rows:
        for m in _DEPRECATES_RE.finditer(r.get("body", "") or ""):
            for token in m.group(1).split():
                deprecated.add(token)
    head_sha = rows[0]["sha"] if rows else None
    visible = [
        {"sha": r["sha"], "author": r["author"], "ts": r["ts"], "message": r["message"]}
        for r in rows
        if r["sha"] not in deprecated
    ]
    return jsonify(path=rel, head_sha=head_sha, commits=visible)


@bp.get("/<doc_id>/history")
@login_required
def document_history(doc_id: str):
    # TODO: shell out to `git log` for the doc path (by id).
    raise NotImplementedError
