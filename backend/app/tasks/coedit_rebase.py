"""Trigger + task for live-rebase (fold an inbound commit into an open session).

``on_wiki_commit`` is called from ``app.wiki.notify.after_doc_write`` on every
wiki commit; if an active session exists for the page and the commit is external
to it, it enqueues ``rebase_coedit_session``. The task runs on
``lightweight_maintenance_queue`` — a rebase only *reads* git refs and updates
the Postgres buffer (no commit, no LLM), so it fits that queue's sub-second
contract and stays off the slower ``documents`` queue for a live feel.

The engine (``app.wiki.coedit_rebase``) does the merge + buffer fold.
"""

from __future__ import annotations

import logging

from app.tasks.queues import documents_queue, lightweight_maintenance_queue
from app.wiki import coedit
from app.wiki.coedit_rebase import RebaseOutcome, rebase_session

log = logging.getLogger(__name__)


@lightweight_maintenance_queue.task()
def rebase_coedit_session(session_id: int, head_sha: str) -> None:
    if rebase_session(session_id, head_sha) == RebaseOutcome.CONFLICT:
        # Overlap → hand to the checkpoint engine's AI-merge (documents_queue).
        # Enqueue by name rather than importing checkpoint_coedit_session: the
        # import would be circular (checkpoint → wiki.utils → notify → here).
        documents_queue.enqueue("checkpoint_coedit_session", (session_id,), {})


def on_wiki_commit(rel_path: str, sha: str) -> None:
    """Enqueue a live-rebase if an active session exists for ``rel_path`` and the
    commit is external to it (its ``base_sha`` hasn't already advanced to ``sha``,
    which is the case for the session's own checkpoint commit)."""
    sess = coedit.get_active_session(rel_path)
    if sess is not None and sess.base_sha != sha:
        rebase_coedit_session(sess.id, sha)
