"""Recording a participant's leave — shared logic + the queued fallback.

The WS disconnect handler is the *only* leave signal (there is no client
leave message). Normally the endpoint's ``finally`` awaits
:func:`record_leave` on a thread and the leave is recorded before the
handler returns. But when the endpoint task is being **cancelled** (server
shutdown; the test portal tearing down a connection), an awaited executor
item can be discarded before any pool worker picks it up — cancelling a
not-yet-started future removes it from the executor queue — and the leave
would silently never run: a phantom participant forever. For that path the
handler enqueues :func:`leave_coedit_session` instead: a synchronous Redis
send that no cancellation can touch, durable across the shutdown that
caused it.

Unlike the OT-era version of this module, ``leave_coedit_session`` does
*not* also attempt a last-leave checkpoint here — a Yjs checkpoint needs the
live ``Doc``, which only exists in whichever process's room registry
(``app/wiki/coedit_ws.py``) holds it, and this queued task may run on a
different one entirely. The normal (non-cancelled) path handles its own
last-leave checkpoint inline in ``app/api/coedit.py``'s ``ws()``, where the
live ``Doc``/tracker are already in scope; the cancelled-fallback path
leaves a dirty session to the periodic scan (``coedit_ws._scan_loop``, a
~300s idle backstop) rather than force a checkpoint from a process that
doesn't hold the room.

``record_leave`` is idempotent on purpose — cancellation can land *after*
the awaited executor item was already picked up, so the queued fallback may
be a second run: the participant delete is delete-if-exists.
"""

from __future__ import annotations

from app.tasks.queues import coedit_queue
from app.wiki import coedit, coedit_ws


def record_leave(session_id: int, user_id: str, path: str) -> None:
    """Mark ``user_id`` gone from ``session_id``. Only acts when their last
    connection to ``path`` has closed, so a second tab doesn't evict them.
    Idempotent (see module docstring)."""
    if coedit_ws.user_still_connected(path, user_id):
        return
    coedit.leave(session_id, user_id)


@coedit_queue.task()
def leave_coedit_session(session_id: int, user_id: str, path: str) -> None:
    """Queued leave — the WS handler's fallback when its own task is being
    cancelled. See module docstring for why this doesn't also checkpoint."""
    record_leave(session_id, user_id, path)
