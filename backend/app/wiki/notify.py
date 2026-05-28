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

from app.db import fts
from app.mcp_server import pubsub as mcp_pubsub
from app.tasks.reindex import index_path
from app.tasks.triggers import fan_out_trigger_eval
from app.wiki import acl
from app.wiki.types import ChangeKind


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

    Also drops the page's owner + page-level ACL rows. Folder ACLs
    above the page are untouched.

    MCP-side: subscribers to the deleted path get one final
    ``notifications/resources/updated`` with ``changeKind="delete"`` so
    they can drop their subscription cleanly. The tree shape changed,
    so a ``list_changed`` follows.
    """
    if not rel_path.endswith(".md"):
        return
    fts.delete_document(rel_path)
    acl.on_page_deleted(rel_path)
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

    ACL rows and owner rows keyed by path are rewritten in one pass via
    ``acl.on_path_moved`` so a rename doesn't strand permissions on a
    no-longer-existing path.

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
    if list_changed:
        mcp_pubsub.publish_list_changed()
