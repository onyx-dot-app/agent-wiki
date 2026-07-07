"""Checkpoint triggers — when the co-edit checkpoint engine fires.

The engine (``app/wiki/coedit_checkpoint.py``) commits a session's live buffer
to git. This wires up *when* that happens:

* a periodic scan checkpoints dirty sessions that have gone idle or are overdue,
* the last-participant-leave and explicit-save paths enqueue ``checkpoint_coedit_session``.

Checkpointing does a git commit (and maybe an LLM merge), so it runs on its
own ``coedit_queue`` — never inline in a web request. It gets a dedicated queue
(not ``documents_queue``) so a session's committed page can't go stale behind
hours of connector ingest; see ``app/tasks/queues.py``.
"""

from __future__ import annotations

import logging

from app.tasks.queue import crontab
from app.tasks.queues import coedit_queue
from app.wiki import coedit
from app.wiki.coedit_checkpoint import checkpoint_session

log = logging.getLogger(__name__)

# Keep git history proportional to editing *sessions*, not keystrokes. The
# live buffer in Postgres is the durable "stash"; git commits only mark real
# boundaries, so history stays at ~one commit per session. The primary trigger
# is on-last-leave; these timers are coarse backstops, not autosave.
#
# Idle: no *edit* for this long → flush and commit. Catches a session that
# never formally closes (a tab left open keeps the SSE alive, so leave never
# fires) once editing has clearly stopped.
#
# Max-interval: the only timer that commits *mid-session* during genuinely
# continuous editing — a safety valve so agents/readers reading git HEAD don't
# see a multi-hour session's stale content indefinitely. Coarse on purpose;
# a marathon session yields a handful of commits, not a stream.
#
# The scan is minute-granular, so both fire "within a minute of" the threshold.
_IDLE_SECONDS = 300
_MAX_INTERVAL_SECONDS = 900


@coedit_queue.task()
def checkpoint_coedit_session(session_id: int) -> None:
    """Commit a session's buffer, then close it if everyone has since left.

    Re-checks participants after committing so a rejoin during the (brief)
    enqueue window keeps the session open. Closes only if still clean, so a late
    op landing after the checkpoint isn't sealed in a closed session (see
    ``close_if_clean``)."""
    checkpoint_session(session_id)
    if not coedit.list_participants(session_id):
        coedit.close_if_clean(session_id)


@coedit_queue.periodic_task(crontab(minute="*"))
def scan_and_checkpoint() -> None:
    """Enqueue a checkpoint for every dirty session that's idle or overdue."""
    due = coedit.sessions_due_for_checkpoint(
        idle_seconds=_IDLE_SECONDS, max_interval_seconds=_MAX_INTERVAL_SECONDS
    )
    for sess in due:
        checkpoint_coedit_session(sess.id)
    if due:
        log.info("coedit checkpoint scan: enqueued %d session(s)", len(due))
