"""Checkpoint triggers — when the co-edit checkpoint engine fires.

The engine (``app/wiki/coedit_checkpoint.py``) commits a session's live buffer
to git. This wires up *when* that happens:

* a periodic scan checkpoints dirty sessions that have gone idle or are overdue,
* the last-participant-leave and explicit-save paths enqueue ``checkpoint_coedit_session``.

Checkpointing does a git commit (and maybe an LLM merge), so it runs on
``documents_queue`` — never inline in a web request.
"""

from __future__ import annotations

import logging

from app.tasks.queue import crontab
from app.tasks.queues import documents_queue
from app.wiki import coedit
from app.wiki.coedit_checkpoint import checkpoint_session

log = logging.getLogger(__name__)

# Commit a session ~after it settles, and never let a continuously-edited
# session go uncommitted longer than the cap. The periodic scan is minute-
# granular, so these are effectively "within a minute of going idle / of the
# cap"; the on-last-leave path commits promptly without waiting for the scan.
_IDLE_SECONDS = 20
_MAX_INTERVAL_SECONDS = 120


@documents_queue.task()
def checkpoint_coedit_session(session_id: int) -> None:
    """Commit a session's buffer, then close it if everyone has since left.

    Re-checks participants after committing so a rejoin during the (brief)
    enqueue window keeps the session open."""
    checkpoint_session(session_id)
    if not coedit.list_participants(session_id):
        coedit.close_session(session_id)


@documents_queue.periodic_task(crontab(minute="*"))
def scan_and_checkpoint() -> None:
    """Enqueue a checkpoint for every dirty session that's idle or overdue."""
    due = coedit.sessions_due_for_checkpoint(
        idle_seconds=_IDLE_SECONDS, max_interval_seconds=_MAX_INTERVAL_SECONDS
    )
    for sess in due:
        checkpoint_coedit_session(sess.id)
    if due:
        log.info("coedit checkpoint scan: enqueued %d session(s)", len(due))
