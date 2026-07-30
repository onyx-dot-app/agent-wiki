"""Trigger for live-rebase: fold an external commit into an open session.

``on_wiki_commit`` is called from ``app.wiki.notify.after_doc_write`` on every
wiki commit. If an active session exists for the page and the commit is external
to it, the fold is enqueued as an ordinary co-edit queue task.

A queue task is the natural home now. The previous version deliberately was not
one — it fanned out over the realtime bus so that whichever process happened to
hold the session's ``pycrdt.Doc`` could act, and noted that "a queue worker never
holds a room either, so dispatching there would just relocate the same problem".
That reasoning ended with the room: the document is rebuilt on demand from
``(ydoc_snapshot, coedit_updates)``, so any worker can do the work, and the bus
fan-out plus the local-room check have nothing left to resolve.

The engine (``app.wiki.coedit_rebase``) does the merge and emits the fold as a
logged, broadcast Yjs update.
"""

from __future__ import annotations

import logging

from app.tasks.coedit_checkpoint import checkpoint_coedit_session_task
from app.tasks.queues import coedit_queue
from app.wiki import coedit
from app.wiki.coedit_rebase import RebaseOutcome, rebase_session

log = logging.getLogger(__name__)


@coedit_queue.task()
def rebase_coedit_session(session_id: int, head_sha: str) -> None:
    """Fold the commit at ``head_sha`` into ``session_id``'s document.

    No retry loop: the fold is a logged update that commutes with whatever
    clients are appending, so there is no race to lose and nothing to retry.
    """
    outcome = rebase_session(session_id, head_sha)
    if outcome == RebaseOutcome.CONFLICT:
        # Overlap the plain 3-way merge couldn't resolve — hand it to the
        # checkpoint engine, whose AI merge resolves and commits. Enqueued
        # rather than called inline so a long LLM merge doesn't hold this
        # task's slot on the co-edit queue.
        checkpoint_coedit_session_task(session_id)


def on_wiki_commit(rel_path: str, sha: str) -> None:
    """Enqueue a live-rebase if an active session exists for ``rel_path`` and the
    commit is external to it (``base_sha`` hasn't already advanced to ``sha``,
    which is the case for the session's own checkpoint commit)."""
    sess = coedit.get_active_session(rel_path)
    if sess is None or sess.base_sha == sha:
        return
    rebase_coedit_session(sess.id, sha)
