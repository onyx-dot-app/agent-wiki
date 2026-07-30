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

from app.db import fts, page_dirs, provenance as db_provenance
from app.mcp_server import pubsub as mcp_pubsub
from app.tasks import coedit_rebase as coedit_rebase_trigger
# Module import, not name import: automanage tasks -> runner -> executor ->
# notify is a cycle, and a name import here breaks whichever side loads
# second. The attribute resolves at call time (same pattern as
# coedit_rebase below).
from app.tasks import automanage as automanage_tasks
from app.tasks.reindex import drop_page_embedding, index_path
from app.tasks.triggers import fan_out_trigger_eval
from app.tasks.update_frequency import check_update_frequency
from app.triggers import repo as triggers_repo
from app.wiki import (
    acl,
    agent_activity,
    coedit,
    comments,
    constants as wiki_constants,
    doc_ids,
    drafts,
    update_policy,
)
from app.wiki.comment_remap import remap_comments
from app.wiki.provenance_remap import remap_source_ranges
from app.models.wiki import ChangeKind, PathMove

log = logging.getLogger(__name__)


def _rename_provenance_safe(old_path: str, new_path: str) -> None:
    """Follow the provenance ledger to a moved page, but never let a ledger
    failure abort the rest of the move. The ledger is best-effort. A failed
    re-point leaves that page's older rows on the old path, degrading its
    attribution, not the move.
    """
    try:
        db_provenance.rename_doc(old_path, new_path)
    except Exception:
        log.exception("provenance rename failed for %s -> %s", old_path, new_path)


def _delete_provenance_safe(doc_path: str) -> None:
    """Drop provenance for a page that left .md space, but never let a ledger
    failure abort the rest of the move. Best-effort, like the rename."""
    try:
        db_provenance.delete_for_doc(doc_path)
    except Exception:
        log.exception("provenance delete failed for %s", doc_path)


def _remap_source_ranges_safe(path: str) -> None:
    """Re-anchor ingest source ranges inline, but never let a remap failure
    abort a save. A stale range self-heals on the next edit."""
    try:
        remap_source_ranges(path)
    except Exception:
        log.exception("source range remap failed for %s", path)


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
    trigger_coedit_rebase: bool = True,
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
        # Mint a stable id for the page (and seed rows for its ancestor folders).
        doc_ids.mint_for_page(rel_path)
        # Focused Auto Organize check on the new page (case collision, exact
        # duplicate). Only for attributable creations — a human or an agent
        # acting for one (chat/MCP/API all pass the current user). System
        # channels (ingestion, seeds) pass owner_user_id=None and are skipped:
        # they arrive in bursts and have their own reconciliation; the sweep
        # covers whatever they leave behind.
        if owner_user_id is not None:
            automanage_tasks.run_detection_on_create(rel_path, owner_user_id)
    index_path(rel_path)
    fan_out_trigger_eval(rel_path, sha, change_kind, actor)
    # Ingestion churn → check the page's 24h update frequency against the
    # owner's threshold + the admin cap (enqueues a lightweight task).
    if actor == wiki_constants.INGEST_AUTHOR:
        check_update_frequency(rel_path)
    # Drift any comments anchored to this page onto the new body. A no-op on
    # CREATE (no comments yet); the real work is on EDIT.
    _remap_comments_safe(rel_path)
    _remap_source_ranges_safe(rel_path)
    mcp_pubsub.publish_doc_update(rel_path, sha, change_kind)
    # Fold this commit into any open co-edit session for the page (skip for a
    # session's own checkpoint commit — trigger_coedit_rebase=False).
    if trigger_coedit_rebase:
        coedit_rebase_trigger.on_wiki_commit(rel_path, sha)
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
    agent-activity rail, the provenance ledger, template-draft state, and
    per-(user, machine) working-dir bindings. Comments are kept as tombstones (orphaned, not
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
    drop_page_embedding(rel_path)
    acl.on_page_deleted(rel_path)
    update_policy.on_page_deleted(rel_path)
    # Tombstone the id (kept, not dropped) so it still resolves — to a deleted
    # state — and a later restore can re-bind it.
    doc_ids.on_deleted(rel_path)
    # The body is gone, so there's nothing to re-anchor against — orphan the
    # page's comments (keeps them as tombstones) rather than dropping them.
    comments.orphan_all_for_doc(rel_path)
    # No-TTL pointers (drafts, working-dirs) would otherwise mis-bind a page
    # later recreated at this path; agent_activity is TTL'd but cleared here
    # for symmetry with the move-out-of-.md-space path.
    agent_activity.delete_for_doc(rel_path)
    _delete_provenance_safe(rel_path)
    drafts.delete(rel_path)
    page_dirs.delete_all_for_page(rel_path)
    fan_out_trigger_eval(rel_path, sha, ChangeKind.DELETE, actor)
    mcp_pubsub.publish_doc_delete(rel_path, sha)
    mcp_pubsub.publish_list_changed()


def after_doc_trashed(
    moves: list[PathMove],
    sha: str,
    actor: str | None,
    *,
    root_move: PathMove | None = None,
) -> None:
    """Side effects when items are moved into ``.trash/`` (soft delete).

    Trashing is a move, so this **re-points** the path-keyed metadata (ACL,
    owner, policy, comments, activity, drafts, working-dirs) to the trash
    location — that's what makes restore lossless: moving back re-points it
    all to the original path. But unlike a normal move, the destination is
    hidden, so we do **not** index or announce the ``.trash/`` copy; instead
    we drop the item from search and fire a ``delete`` event on the old path,
    so it disappears from search / triggers / live views. Restore reuses the
    normal ``after_path_move`` (moving out of ``.trash/`` re-indexes at the
    original path).

    ``root_move`` is the folder/page root that was trashed (``rel`` →
    ``.trash/<id>/rel``). Like ``after_path_move``, it's needed so a folder's
    own ACL/policy row re-points to the trash location even when all its files
    sit in a subdirectory — otherwise it strands at the (now gone) original
    path and ``_trash_perms`` mis-authorizes the trashed folder. See
    ``acl.on_path_moved``.
    """
    acl.on_path_moved(moves, root_move=root_move)
    update_policy.on_path_moved(moves, root_move=root_move)
    # coedit.py is pure DB bookkeeping (no pycrdt import) and so can't evict
    # a superseded session's in-memory room itself — evict here, for each
    # A superseded session (a destination collision, e.g. someone opened the
    # just-moved-to path in the seconds-wide window before this move landed) is
    # closed in the DB by on_path_moved and needs nothing else: no process holds
    # a live document to evict.
    coedit.on_path_moved(moves)
    # Tombstone the id(s) at the *original* root rather than following the move
    # into `.trash/` — the id keeps resolving (to a deleted state), and restore
    # re-binds it. ACL/policy above deliberately follow into `.trash/` (so the
    # Trash view can authorize); ids deliberately don't.
    if root_move is not None:
        doc_ids.on_deleted(root_move.old)
    for mv in moves:
        if not mv.old.endswith(".md"):
            continue
        # Leave search/live: drop the index + embedding, fire delete, notify.
        fts.delete_document(mv.old)
        drop_page_embedding(mv.old)
        fan_out_trigger_eval(mv.old, sha, ChangeKind.DELETE, actor)
        mcp_pubsub.publish_doc_delete(mv.old, sha)
        # Carry the durable metadata to the trash path so restore recovers it
        # (do NOT re-anchor/re-index it — the .trash/ copy stays hidden).
        comments.reassign_doc_path(mv.old, mv.new)
        agent_activity.rename_doc(mv.old, mv.new)
        _rename_provenance_safe(mv.old, mv.new)
        drafts.rename(mv.old, mv.new)
        page_dirs.rename_page(old_wiki_path=mv.old, new_wiki_path=mv.new)
    # Trigger YAMLs moved into .trash are excluded from loading, so reconverging
    # the cache from disk drops the trashed triggers (they stop firing).
    try:
        triggers_repo.rebuild_from_filesystem()
    except Exception:
        log.exception("trigger cache rebuild after trash failed")
    mcp_pubsub.publish_list_changed()


def after_path_move(
    moves: list[PathMove],
    sha: str,
    actor: str | None,
    *,
    root_move: PathMove | None = None,
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
    ACL + owner rows in one pass via ``acl.on_path_moved``; co-edit
    sessions (so live buffers and their queued checkpoints follow the
    page instead of resurrecting the old path); comments; the
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
    acl.on_path_moved(moves, root_move=root_move)
    update_policy.on_path_moved(moves, root_move=root_move)
    doc_ids.on_path_moved(moves, root_move=root_move)
    coedit.on_path_moved(moves)
    list_changed = False
    for mv in moves:
        old_p, new_p = mv.old, mv.new
        old_is_md = old_p.endswith(".md")
        new_is_md = new_p.endswith(".md")
        if old_is_md:
            fts.delete_document(old_p)
            drop_page_embedding(old_p)
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
            _rename_provenance_safe(old_p, new_p)
            # Ranges are re-keyed to new_p now, so the remap query finds them.
            _remap_source_ranges_safe(new_p)
            drafts.rename(old_p, new_p)
            page_dirs.rename_page(old_wiki_path=old_p, new_wiki_path=new_p)
        elif old_is_md:
            # The page left .md-space (renamed to a non-doc path) — there's no
            # new doc to carry these onto, so drop them rather than strand them
            # on a path that no longer exists.
            comments.orphan_all_for_doc(old_p)
            agent_activity.delete_for_doc(old_p)
            _delete_provenance_safe(old_p)
            drafts.delete(old_p)
            page_dirs.delete_all_for_page(old_p)
    # Triggers store their scope_path inside the moved YAML (and the YAML's
    # location is derived from it), so a git mv leaves scopes dangling. Rewrite
    # the affected YAMLs and patch their cache rows in step — proportional to
    # what moved, not the whole table. If that raises partway, fall back to a
    # full reconverge so a partial repoint can't leave the cache stale.
    try:
        triggers_repo.repoint_scopes_for_moves(moves, actor=actor)
    except Exception:
        log.exception("trigger scope repoint after path move failed; reconverging cache")
        try:
            triggers_repo.rebuild_from_filesystem()
        except Exception:
            log.exception("fallback trigger cache rebuild after path move failed")
    if list_changed:
        mcp_pubsub.publish_list_changed()
