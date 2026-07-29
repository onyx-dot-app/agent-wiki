"""Recording a co-edit connection's leave — shared logic + queued fallback.

The WS disconnect handler is the *only* leave signal (there is no client
leave message). Normally the endpoint's ``finally`` awaits
:func:`record_leave` on a thread and the leave is recorded before the
handler returns. But when the endpoint task is being **cancelled** (server
shutdown; the test portal tearing down a connection), an awaited executor
item can be discarded before any pool worker picks it up — cancelling a
not-yet-started future removes it from the executor queue — and the shared
connection lease would otherwise linger until heartbeat expiry. For that path
the handler enqueues :func:`leave_coedit_session` instead: a synchronous Redis
send that no cancellation can touch, durable across the shutdown that caused
it.

``record_leave`` is idempotent on purpose — cancellation can land *after*
the awaited executor item was already picked up, so the queued fallback may be
a second run. Only the last process-shared lease removes the participant, and
only the last participant leaving triggers a checkpoint.
"""

from __future__ import annotations

import logging

from app.tasks.coedit_checkpoint import checkpoint_coedit_session
from app.tasks.queues import coedit_queue
from app.wiki import coedit, coedit_channel

log = logging.getLogger(__name__)


def record_leave(connection_id: str) -> None:
    """Remove one shared connection lease and checkpoint on the final leave.

    Idempotent: normal teardown and its cancellation fallback may both run.
    """
    outcome = coedit.disconnect_connection(connection_id)
    if outcome.session_id is None:
        return
    if outcome.participant_removed:
        coedit_channel.broadcast_presence(outcome.session_id)
    if not outcome.session_empty:
        return
    # Best-effort: the participant row is already gone, so a failed enqueue
    # (e.g. a full queue) must not fail the leave. The periodic scan is the
    # backstop — the session is dirty, so it's recovered once idle.
    try:
        checkpoint_coedit_session(outcome.session_id)
    except Exception:
        log.exception(
            "coedit: checkpoint enqueue failed on last-leave for session %s",
            outcome.session_id,
        )


@coedit_queue.task()
def leave_coedit_session(session_id: int, connection_id: str) -> None:
    """Queued leave — the WS handler's fallback when its own task is being
    cancelled.

    The two-argument envelope is safe during a rolling deploy. Connection ids
    are ``conn_``-prefixed and therefore cannot match a user id if an older
    worker treats the second argument as one. A current worker needs only the
    connection id; the session id preserves that compatible envelope.
    """
    del session_id
    record_leave(connection_id)
