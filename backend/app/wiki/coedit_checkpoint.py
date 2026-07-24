"""Checkpoint a co-edit session's live Yjs doc back into git.

The live doc lives in memory (owned by the process holding the room, see
``app/wiki/coedit_ws.py``), durably logged to Postgres as an update stream
(``coedit_updates``) plus periodic snapshots. A checkpoint is the Layer-2
boundary that splices it into markdown and commits it to git through the
*existing* write gateway, reconciling any agent/ingest commit that landed
meanwhile via the same 3-way + AI merge. Durability is Postgres, so a
checkpoint is about visibility (making the committed page fresh for
readers/search/agents) and bounding merge size — not data safety.

Attribution: the commit author is the last editor; other session
participants are added as ``Co-authored-by:`` trailers where known. See
``Engineering Projects/Agent Wiki Project/design/Co-Editing.md``.

This module is the engine (``checkpoint_ydoc_session``). What *triggers* it
— a periodic scan, last-participant-leave, an inbound-commit reconciliation
— is wired separately (``app/wiki/coedit_ws.py``'s scan loop,
``app/api/coedit.py``'s teardown, ``app/tasks/coedit_rebase.py``).

Architectural notes this had to validate rather than assume:

1. The live ``pycrdt`` ``Doc`` and its ``TouchedTracker`` exist only in the
   web process's memory, owned by the WebSocket connection(s) for a session
   (see ``app/wiki/coedit_ws.py``) — a *separate* ``worker-light`` process
   (where a generic ``coedit_queue`` task normally runs) cannot reach them.
   So the splice step here cannot be dispatched through the task queue on
   its own — it must run in the same process that holds the doc; only the
   final git-commit step is safe to hand off (see below).
2. Less obviously: pycrdt's objects (``Doc``, its ``Subscription``s, every
   ``Xml*`` node) are thread-affine — a PyO3/Rust "unsendable" type. Moving
   the splice step to a worker *thread* crashes as soon as anything tries to
   touch the doc or drop a ``Subscription`` from that thread. So
   ``checkpoint_ydoc_session`` below is an async function that does every
   doc-touching step (``checkpoint_body``, ``tracker.reset()``,
   ``doc.get_update()``) directly on the caller's thread/event loop — the
   *only* part it offloads via ``anyio.to_thread.run_sync`` is
   ``commit_and_fan_out``, which operates on plain strings and does the
   actual git/LLM I/O.

A real multi-worker deployment of this path needs every checkpoint trigger
(idle/interval/last-leave/inbound-commit) driven from within the owning web
process itself, not a cron-style worker scan — ``coedit_ws.py``'s own scan
loop already does this (filtered to rooms it actually holds); a session held
by a *different* process is picked up by that process's own scan instead.
"""

from __future__ import annotations

import logging

import anyio
from pycrdt import Doc

from app.auth import User, set_current_user
from app.auth import users as users_repo
from app.models.wiki import ChangeKind, CommitResult
from app.wiki import coedit, filesystem
from app.wiki import drafts as wiki_drafts
from app.wiki import git as wiki_git
from app.wiki.markdown_splice import TouchedTracker, checkpoint_body
from app.wiki.utils import commit_and_fan_out

log = logging.getLogger(__name__)


def _user(user_id: str) -> User | None:
    row = users_repo.get_by_id(user_id)
    if row is None:
        return None
    return User(
        id=row["id"], email=row["email"], name=row["name"], is_admin=bool(row["is_admin"])
    )


def _commit_message(session_id: int, *, primary_author_id: str | None) -> str:
    """`Co-authored-by:` trailers for every participant except the primary
    author (git credits the primary author separately via the commit author)."""
    lines = ["Co-editing checkpoint"]
    trailers: list[str] = []
    for p in coedit.list_participants(session_id):
        if p.user_id == primary_author_id:
            continue
        u = users_repo.get_by_id(p.user_id)
        if u is not None:
            trailers.append(f"Co-authored-by: {u['name'] or u['email']} <{u['email']}>")
    if trailers:
        lines.append("")
        lines.extend(trailers)
    return "\n".join(lines)


async def checkpoint_ydoc_session(
    session_id: int, *, doc: Doc, tracker: TouchedTracker, author_user_id: str | None
) -> str | None:
    """Splice + commit a session's live doc to git; return the new sha (or
    ``None`` on a no-op).

    Serialized per session by an advisory lock, so two processes that both
    trigger a checkpoint for the same session can't both commit — the loser
    blocks, then re-reads a clean/closed session and no-ops (see
    ``coedit.checkpoint_lock``). Distinct sessions still checkpoint in
    parallel. See the module note above for why this is ``async`` — only the
    final git-commit step may run off-thread; everything touching
    ``doc``/``tracker`` must stay on the caller's thread.
    """
    with coedit.checkpoint_lock(session_id) as acquired:
        if not acquired:
            # Another process is checkpointing this session and held the
            # lock past the wait cap. Skip — the periodic scan re-triggers
            # if still dirty.
            log.info("coedit ydoc checkpoint: session %s busy; skipping", session_id)
            return None
        return await _checkpoint_ydoc_locked(session_id, doc, tracker, author_user_id)


async def _checkpoint_ydoc_locked(
    session_id: int, doc: Doc, tracker: TouchedTracker, author_user_id: str | None
) -> str | None:
    """Body of ``checkpoint_ydoc_session``, run while holding the session's
    checkpoint advisory lock so the read-guard-commit sequence is atomic
    across processes."""
    sess = coedit.get_session(session_id)
    if sess is None:
        return None  # gone
    if sess.status != coedit.SessionStatus.ACTIVE.value:
        # A closed session is finalized — never re-commit it. This dedupes
        # duplicate triggers: once the first checkpoint commits and closes
        # the session, every other trigger no-ops here. A closed session
        # should already be clean (close follows a clean checkpoint); if
        # it's somehow dirty, log and skip rather than clobber HEAD with its
        # stale doc — the edits stay in the update log for manual recovery.
        if sess.ydoc_seq != sess.ydoc_checkpointed_seq:
            log.warning(
                "coedit ydoc checkpoint: session %s is %s but dirty (seq %d != "
                "checkpointed seq %d); skipping — needs manual reconciliation",
                session_id,
                sess.status,
                sess.ydoc_seq,
                sess.ydoc_checkpointed_seq,
            )
        return None
    state = coedit.get_ydoc_state(session_id)
    if state is None or state.seq == state.checkpointed_seq:
        return None  # nothing new to commit

    path = sess.path
    # The page existed when the session was seeded (base_sha set) but the
    # working-tree file is gone — it was moved or deleted underneath the
    # session. A move should have re-keyed the session
    # (``coedit.on_path_moved``); committing here would resurrect the dead
    # path from the doc. Working-tree ``is_file`` (not a git read) so a
    # transient git failure can't masquerade as a missing page; an OSError
    # propagates and the caller retries. Participant-less sessions would
    # normally close (the zombie case), but that decision lives with the
    # caller (see api/coedit.py's last-leave path) — here we just skip.
    if sess.base_sha is not None and not filesystem.absolute(path).is_file():
        log.warning(
            "coedit ydoc checkpoint: session %s targets missing path %r; skipping "
            "(scan retries after any move re-key)",
            session_id,
            path,
        )
        return None

    # Merge base = the page content at the last checkpoint. None when the
    # session was opened on a not-yet-existing page (→ create).
    base_body = wiki_git.read_file_opt(path, ref=sess.base_sha) if sess.base_sha else None
    change_kind = ChangeKind.EDIT if sess.base_sha else ChangeKind.CREATE
    new_body = checkpoint_body(base_body or "", doc, tracker)  # doc-touching: stays on this thread

    author = _user(author_user_id) if author_user_id else None
    message = _commit_message(session_id, primary_author_id=author_user_id)

    def _commit() -> CommitResult | None:
        # System-initiated write: editors' write permission was already
        # enforced per-update when they applied it, so skip the ACL gate;
        # not "agent activity".
        with set_current_user(author):
            return commit_and_fan_out(
                path,
                new_body,
                message,
                change_kind=change_kind,
                base_body=base_body,
                ai_merge=True,
                skip_acl=True,
                record_activity=False,
                # This is the session's own commit — don't fold it back into
                # the session as an inbound rebase (live-rebase handles the
                # other direction, an external commit landing on this page).
                trigger_coedit_rebase=False,
            )

    result = await anyio.to_thread.run_sync(_commit)

    seq = state.seq
    if result is not None:
        # Reset the tracker now that its base_body reference (the text this
        # checkpoint committed against) is stale — the next checkpoint's
        # untouched-region guarantee is relative to result.new_body, not the
        # base_body just committed. Both touch the doc/tracker — must run
        # here, not in the thread that just ran _commit.
        tracker.reset()
        snapshot = bytes(doc.get_update())
        coedit.checkpoint_ydoc(session_id, snapshot=snapshot, base_sha=result.sha, seq=seq)
        # Clear the template-drafting row once the committed body diverges
        # from the snapshot — the page is now the user's own, so the chat
        # banner and the template's system-prompt override should no longer
        # apply.
        wiki_drafts.clear_if_diverged(path, result.new_body)
        return result.sha

    # None = the merge produced exactly the current HEAD (doc already
    # matches committed content). Still mark clean against current HEAD so
    # we don't re-attempt this seq forever.
    head = wiki_git.head_sha_for_path(path)
    if head is not None:
        tracker.reset()
        snapshot = bytes(doc.get_update())
        coedit.checkpoint_ydoc(session_id, snapshot=snapshot, base_sha=head, seq=seq)
    else:
        # Shouldn't happen — a no-op merge implies HEAD exists. Surface it
        # rather than silently leaving the session dirty and re-entering
        # this path on every future trigger.
        log.warning(
            "coedit ydoc checkpoint: no-op merge but no HEAD for %s (session %s); left dirty",
            path,
            session_id,
        )
    return None
