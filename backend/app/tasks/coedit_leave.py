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

Both paths now enqueue the checkpoint the same way (rather than one
attempting it in-process and the other skipping it): the checkpoint engine
(``app/wiki/coedit_checkpoint.py``) rebuilds its own throwaway ``Doc`` from
the session's persisted (snapshot, updates) and never touches any process's
live room, so a worker process — which never holds one — checkpoints a
session exactly as well as the process that does. That closes what used to
be a real gap: previously, a shutdown landing between the cancellation and
this fallback firing meant edits since the last checkpoint were lost
outright, since the fallback had nothing local to checkpoint from.
"""

from __future__ import annotations

import asyncio
import logging

from app.tasks.coedit_checkpoint import checkpoint_coedit_session_task
from app.tasks.queues import coedit_queue
from app.wiki import coedit, coedit_channel

log = logging.getLogger(__name__)


def _do_leave(session_id: int, user_id: str) -> bool:
    """Leave bookkeeping shared by both the normal and queued-fallback
    paths. Only acts when the user's last connection has closed, so a
    second tab doesn't evict them. Returns True if the departing user was
    the last participant — the caller should enqueue a checkpoint."""
    if coedit_channel.user_still_connected(session_id, user_id):
        return False
    coedit.leave(session_id, user_id)
    coedit_channel.broadcast_presence(session_id)
    return not coedit.list_participants(session_id)


async def record_leave(session_id: int, user_id: str) -> None:
    """Mark ``user_id`` gone from ``session_id`` and enqueue a checkpoint if
    they were the last one out. Idempotent (see module docstring).

    Enqueues rather than awaiting the checkpoint in-process: a plain Redis
    send, not itself at risk of the same cancellation race this function
    exists to guard the *leave* bookkeeping against, and the checkpoint no
    longer needs to run in this specific process anyway (see module
    docstring) — no reason to keep the WS teardown path blocked on it.
    """
    last_out = await asyncio.to_thread(_do_leave, session_id, user_id)
    if last_out:
        await asyncio.to_thread(checkpoint_coedit_session_task, session_id)


@coedit_queue.task()
def leave_coedit_session(session_id: int, user_id: str) -> None:
    """Queued leave — the WS handler's fallback when its own task is being
    cancelled (see module docstring). Enqueues a checkpoint too if this was
    the last participant out, same as the normal path — no longer skipped:
    the checkpoint engine never needed this process to hold the session's
    room, it needed the fallback to still not have known that."""
    if _do_leave(session_id, user_id):
        checkpoint_coedit_session_task(session_id)
