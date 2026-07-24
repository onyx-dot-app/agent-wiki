"""Trigger + task for live-rebase (react to an inbound commit landing under an
open session).

``on_wiki_commit`` is called from ``app.wiki.notify.after_doc_write`` on every
wiki commit; if an active session exists for the page and the commit is external
to it, it enqueues ``rebase_coedit_session``. The task runs on
``lightweight_maintenance_queue`` — deciding whether a checkpoint is needed
only *reads* git refs (no commit, no LLM), so it fits that queue's sub-second
contract and stays off the slower ``documents`` queue for a live feel.

The actual reconciliation can't be dispatched through a task queue at all —
a Yjs checkpoint needs the live ``Doc``, which exists only in whichever
process's room registry (``app/wiki/coedit_ws.py``) holds it, and this task
may run on a different process (``lightweight_maintenance_queue`` lives on
``worker-light``, a separate process from the one backend replica that
serves WebSocket connections). So instead of enqueuing, this emits on the
realtime bus (``app/realtime/bus.py``) — the same Postgres LISTEN/NOTIFY
mechanism the prior SSE transport used for its own cross-process delivery —
and ``coedit_ws.py`` reacts if (and only if) it's the process holding that
page's room.

The engine (``app.wiki.coedit_rebase``) decides when a checkpoint is needed.
"""

from __future__ import annotations

import logging

from app.realtime import bus
from app.tasks.queues import lightweight_maintenance_queue
from app.wiki import coedit
from app.wiki.coedit_rebase import RebaseOutcome, rebase_session

log = logging.getLogger(__name__)


@lightweight_maintenance_queue.task()
def rebase_coedit_session(session_id: int, head_sha: str, path: str) -> None:
    if rebase_session(session_id, head_sha) == RebaseOutcome.NEEDS_CHECKPOINT:
        bus.emit({"kind": "coedit_checkpoint_needed", "path": path})


def on_wiki_commit(rel_path: str, sha: str) -> None:
    """Enqueue a live-rebase if an active session exists for ``rel_path`` and the
    commit is external to it (its ``base_sha`` hasn't already advanced to ``sha``,
    which is the case for the session's own checkpoint commit)."""
    sess = coedit.get_active_session(rel_path)
    if sess is not None and sess.base_sha != sha:
        rebase_coedit_session(sess.id, sha, rel_path)
