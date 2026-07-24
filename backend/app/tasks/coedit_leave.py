"""Recording a participant's leave — shared logic + the queued fallback.

The WS disconnect handler is the *only* leave signal (there is no client
leave message). Normally the endpoint's ``finally`` awaits
:func:`record_leave` on a thread and the leave is recorded before the
handler returns. But when the endpoint task is being **cancelled** (server
shutdown; the test portal tearing down a connection), an awaited executor
item can be discarded before any pool worker picks it up — cancelling a
not-yet-started future removes it from the executor queue — and the leave
would silently never run: a phantom participant forever, and a dirty buffer
that never gets its last-leave checkpoint. For that path the handler
enqueues :func:`leave_coedit_session` instead: a synchronous Redis send that
no cancellation can touch, durable across the shutdown that caused it.

``record_leave`` is idempotent on purpose — cancellation can land *after*
the awaited executor item was already picked up, so the queued fallback may
be a second run: the participant delete is delete-if-exists, the presence
broadcast is harmless, and the checkpoint no-ops on a clean buffer.
"""

from __future__ import annotations

import logging

from app.tasks.coedit_checkpoint import checkpoint_coedit_session
from app.tasks.queues import coedit_queue
from app.wiki import coedit, coedit_channel

log = logging.getLogger(__name__)


def record_leave(session_id: int, user_id: str) -> None:
    """Mark ``user_id`` gone from ``session_id`` and checkpoint if they were
    the last one out. Only acts when their last connection has closed, so a
    second tab doesn't evict them. Idempotent (see module docstring)."""
    if coedit_channel.user_still_connected(session_id, user_id):
        return
    coedit.leave(session_id, user_id)
    coedit_channel.broadcast_presence(session_id)
    if coedit.list_participants(session_id):
        return
    # Best-effort: the participant row is already gone, so a failed enqueue
    # (e.g. a full queue) must not fail the leave. The periodic scan is the
    # backstop — the session is dirty, so it's recovered once idle.
    try:
        checkpoint_coedit_session(session_id)
    except Exception:
        log.exception(
            "coedit: checkpoint enqueue failed on last-leave for session %s",
            session_id,
        )


@coedit_queue.task()
def leave_coedit_session(session_id: int, user_id: str) -> None:
    """Queued leave — the WS handler's fallback when its own task is being
    cancelled. On a worker the channel registry is empty, so the
    still-connected guard in :func:`record_leave` never mistakes another
    process's connections for this user's."""
    record_leave(session_id, user_id)
