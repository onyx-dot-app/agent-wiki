"""Post-write notifications for wiki ``.md`` changes.

Every successful write to a wiki document — UI save, agent edit, agent
write, agent move, agent delete, API delete, API drag-and-drop — must
end with **two** side effects:

1. **FTS reindex** so search results don't go stale.
2. **Trigger fan-out** so users (and downstream services, when outbound
   dispatch lands) hear about the change.

This module is the single seam for those side effects. Callers commit
via ``app.wiki.git`` (the *only* place that shells out to git), then
invoke ``after_doc_write`` / ``after_doc_delete`` here. Folding the
commit into these helpers would be wrong: some commits move many files
in one git operation (``git mv`` of a directory), and trigger
attribution wants to fire per moved file, not once per commit.

Trigger YAMLs (``.trigger_*.yaml``) are committed via the same git
helpers but **must not** flow through here — they're trigger config,
not docs. ``app/triggers/storage.py`` calls ``commit_file`` directly
and never calls this module. That's by design; don't add a "smart"
path filter here that tries to do both.

Why this lives in ``app/wiki/`` and not ``app/llm/agents/tools/``:
both API handlers (`api/wiki.py`) and agent tools (`tools/*.py`)
need it. Putting it under ``tools/`` would couple the API to a tool
package; putting it at the wiki layer keeps the dependency direction
clean (``api/`` → ``wiki/`` ← ``tools/``).
"""
from __future__ import annotations

import logging

from app.db import fts, page_dirs
from app.mcp_server import pubsub as mcp_pubsub
from app.tasks.reindex import index_path
from app.tasks.triggers import fan_out_trigger_eval
from app.triggers import repo as triggers_repo
from app.wiki import acl, agent_activity, comments, drafts
from app.wiki.comment_remap import remap_comments
from app.models.wiki import ChangeKind

log = logging.getLogger(__name__)


def _remap_comments_safe(path: str) -> None:
    """Re-anchor comments inline, but never let a remap failure abort a save.

    A stale comment self-heals on the next edit (and the API read path remaps
    on a stale anchor), so swallowing here only ever costs a brief position
    inaccuracy — never a lost write."""
    try:
        remap_comments(path)
    except Exception:
        log.exception("comment remap failed for %s", path)


def after_doc_write(
    rel_path: str,
    sha: str,
    change_kind: ChangeKind,
    actor: str | None,
    *,
    owner_user_id: str | None = None,
) -> None:
    """Run reindex + trigger fan-out + MCP pub-sub for a wiki ``.md`` write.

    ``change_kind`` is ``"create"`` for a new file, ``"edit"`` for an
    in-place rewrite. The trigger fan-out task interprets these (in
    particular, ``"create"`` against a directory-scope trigger uses the
    new-file evaluator instead of the standard delta flow).

    On ``"create"``, also seeds default-public ACL rows + an owner row
    keyed at ``rel_path``. ``owner_user_id`` is the current user; pass
    ``None`` for system-driven creates (bootstrap, agent activity that
    isn't attributable to a single user).

    The MCP pub-sub publish at the end fans the event out to every MCP
    session subscribed to ``wiki:///<rel_path>`` — see
    ``app.mcp_server.pubsub``. Creates also fire a ``list_changed``
    notification because the tree shape (and therefore
    ``resources/list``) just changed.
    """
    if not rel_path.endswith(".md"):
        return
    if change_kind == ChangeKind.CREATE:
        acl.on_page_created(rel_path, owner_user_id=owner_user_id)
    index_path(rel_path)
    fan_out_trigger_eval(rel_path, sha, change_kind, actor)
    # Drift any comments anchored to this page onto the new body. A no-op on
    # CREATE (no comments yet); the real work is on EDIT.
    _remap_comments_safe(rel_path)
    mcp_pubsub.publish_doc_update(rel_path, sha, change_kind)
    if change_kind == ChangeKind.CREATE:
        mcp_pubsub.publish_list_changed()


def after_doc_delete(rel_path: str, sha: str, actor: str | None) -> None:
    """Run FTS delete + trigger fan-out + MCP pub-sub for a wiki ``.md`` delete.

    Fans out with ``change_kind="delete"`` so triggers can evaluate the
    removal against ``before=<old body>, after=""``. The fan-out task
    reads BEFORE/AFTER from git refs; for a delete commit, ``after`` at
    that sha is empty (the file is gone) and ``before`` at ``sha^`` is
    the body just before deletion.

    Drops every Postgres row that is a *live pointer* to the page: the
    owner + page-level ACL rows (folder ACLs above are untouched), the
    agent-activity rail, template-draft state, and per-(user, machine)
    working-dir bindings. Comments are kept as tombstones (orphaned, not
    deleted) since they have archival value; the rest are operational state
    with none. Point-in-time records (launch history, eval samples) are left
    alone.

    Caller deletes one ``.md`` at a time — for a folder delete it invokes
    this per nested page (see ``api/wiki.py``), so the folder path itself
    never reaches here.

    MCP-side: subscribers to the deleted path get one final
    ``notifications/resources/updated`` with ``changeKind="delete"`` so
    they can drop their subscription cleanly. The tree shape changed,
    so a ``list_changed`` follows.
    """
    if not rel_path.endswith(".md"):
        return
    fts.delete_document(rel_path)
    acl.on_page_deleted(rel_path)
    # The body is gone, so there's nothing to re-anchor against — orphan the
    # page's comments (keeps them as tombstones) rather than dropping them.
    comments.orphan_all_for_doc(rel_path)
    # No-TTL pointers (drafts, working-dirs) would otherwise mis-bind a page
    # later recreated at this path; agent_activity is TTL'd but cleared here
    # for symmetry with the move-out-of-.md-space path.
    agent_activity.delete_for_doc(rel_path)
    drafts.delete(rel_path)
    page_dirs.delete_all_for_page(rel_path)
    fan_out_trigger_eval(rel_path, sha, ChangeKind.DELETE, actor)
    mcp_pubsub.publish_doc_delete(rel_path, sha)
    mcp_pubsub.publish_list_changed()


def after_path_move(
    moves: list[tuple[str, str]], sha: str, actor: str | None
) -> None:
    """Post-move side effects for every ``(old, new)`` pair from a single
    ``git mv`` commit.

    For each pair where either side is a ``.md`` file, this fans out a
    ``"delete"`` event on the old path (so triggers attached to the old
    location or its parent dirs hear about the rename) and a ``"create"``
    event on the new path. FTS is updated to drop the old key and reindex
    the new one.

    Non-``.md`` moves (e.g. ``.gitkeep``) are skipped — they don't carry
    doc content and triggers can't evaluate them.

    Every Postgres row that is a *live pointer* to the page is re-pointed to
    the new path so nothing strands on a path that no longer exists:
    ACL + owner rows in one pass via ``acl.on_path_moved``; comments; the
    agent-activity rail; template-draft state; and per-(user, machine)
    working-dir bindings. Point-in-time records (launch history, ingest eval
    samples, the audit log) are deliberately left alone.

    The trigger cache is reconverged from disk once at the end — trigger
    YAMLs ride along with their folder in the ``git mv``, but their stored
    ``file_path`` is absolute. Doing it here (rather than in the API route)
    means the agent-tool move path gets it too.

    MCP-side: each affected path gets the corresponding update or
    delete, and a single ``list_changed`` is fired at the end (the
    tree shape changed once, even if many paths moved).
    """
    acl.on_path_moved(moves)
    list_changed = False
    for old_p, new_p in moves:
        old_is_md = old_p.endswith(".md")
        new_is_md = new_p.endswith(".md")
        if old_is_md:
            fts.delete_document(old_p)
            fan_out_trigger_eval(old_p, sha, ChangeKind.DELETE, actor)
            mcp_pubsub.publish_doc_delete(old_p, sha)
            list_changed = True
        if new_is_md:
            index_path(new_p)
            fan_out_trigger_eval(new_p, sha, ChangeKind.CREATE, actor)
            mcp_pubsub.publish_doc_update(new_p, sha, ChangeKind.CREATE)
            list_changed = True
        if old_is_md and new_is_md:
            # Re-key comments to the new path, then re-anchor them (a rename
            # commit may also change content; path_at_ref follows the rename).
            comments.reassign_doc_path(old_p, new_p)
            _remap_comments_safe(new_p)
            # Other live pointers follow the page to its new path.
            agent_activity.rename_doc(old_p, new_p)
            drafts.rename(old_p, new_p)
            page_dirs.rename_page(old_wiki_path=old_p, new_wiki_path=new_p)
        elif old_is_md:
            # The page left .md-space (renamed to a non-doc path) — there's no
            # new doc to carry these onto, so drop them rather than strand them
            # on a path that no longer exists.
            comments.orphan_all_for_doc(old_p)
            agent_activity.delete_for_doc(old_p)
            drafts.delete(old_p)
            page_dirs.delete_all_for_page(old_p)
    # Triggers store their scope_path inside the moved YAML (and the YAML's
    # location is derived from it), so a git mv leaves scopes dangling. Rewrite
    # the affected YAMLs, then reconverge the absolute file_path cache from
    # disk. Both best-effort, in *separate* try blocks: a partial repoint
    # failure must still let the rebuild run, or the cache would stay stale
    # against the YAMLs that were rewritten until some later incidental rebuild.
    try:
        triggers_repo.repoint_scopes_for_moves(moves, actor=actor)
    except Exception:
        log.exception("trigger scope repoint after path move failed")
    try:
        triggers_repo.rebuild_from_filesystem()
    except Exception:
        log.exception("trigger cache rebuild after path move failed")
    if list_changed:
        mcp_pubsub.publish_list_changed()
