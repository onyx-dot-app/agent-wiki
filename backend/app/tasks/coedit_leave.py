"""Recording a participant's leave — shared logic + the queued fallback.

The WS disconnect handler is the *only* leave signal (there is no client
leave message). Normally the endpoint's ``finally`` awaits
:func:`record_leave` directly (it's on the event loop task already — see
``app/api/coedit.py``) and the leave is recorded before the handler returns.
But when the endpoint task is being **cancelled** (server shutdown; the test
portal tearing down a connection), cancellation can land inside
:func:`record_leave`'s own first ``asyncio.to_thread`` call before that
executor item is even picked up — cancelling a not-yet-started future
removes it from the executor queue — and the leave would silently never
run: a phantom participant forever, and a dirty document that never gets
its last-leave checkpoint. For that path the handler enqueues
:func:`leave_coedit_session` instead: a synchronous Redis send that no
cancellation can touch, durable across the shutdown that caused it.

``record_leave`` is idempotent on purpose — cancellation can land *after*
the leave bookkeeping was already applied, so the queued fallback may be a
second run: the participant delete is delete-if-exists, the presence
broadcast is harmless.

The queued fallback runs on a *worker* process, which never holds the
session's in-memory room (see ``app/wiki/coedit_room.py``) — so unlike
``record_leave``, it never attempts a checkpoint; there is nothing local to
checkpoint from. If the owning web process is mid-shutdown when this
fallback fires, edits since the last checkpoint are lost, bounded by
checkpoint frequency — the same accepted tradeoff documented in
``app/wiki/coedit.py``'s module docstring.
"""

from __future__ import annotations

import asyncio
import logging

from app.tasks.coedit_checkpoint import checkpoint_coedit_session
from app.tasks.queues import coedit_queue
from app.wiki import coedit, coedit_channel

log = logging.getLogger(__name__)


def _do_leave(session_id: int, user_id: str) -> bool:
    """Leave bookkeeping shared by both the normal and queued-fallback
    paths. Only acts when the user's last connection has closed, so a
    second tab doesn't evict them. Returns True if the departing user was
    the last participant — the caller should attempt a checkpoint."""
    if coedit_channel.user_still_connected(session_id, user_id):
        return False
    coedit.leave(session_id, user_id)
    coedit_channel.broadcast_presence(session_id)
    return not coedit.list_participants(session_id)


async def record_leave(session_id: int, user_id: str) -> None:
    """Mark ``user_id`` gone from ``session_id`` and checkpoint if they were
    the last one out. Idempotent (see module docstring)."""
    last_out = await asyncio.to_thread(_do_leave, session_id, user_id)
    if not last_out:
        return
    # Best-effort: the participant row is already gone, so a failed
    # checkpoint must not fail the leave. The periodic scan is the backstop
    # — the session is dirty, so it's recovered once idle.
    try:
        await checkpoint_coedit_session(session_id)
    except Exception:
        log.exception(
            "coedit: checkpoint failed on last-leave for session %s", session_id
        )


@coedit_queue.task()
def leave_coedit_session(session_id: int, user_id: str) -> None:
    """Queued leave — the WS handler's fallback when its own task is being
    cancelled (see module docstring). No checkpoint attempt: this runs on a
    worker process, which never holds the session's in-memory room."""
    _do_leave(session_id, user_id)
