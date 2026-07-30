"""Trigger + cross-process fan-out for co-edit checkpointing.

The engine (``app/wiki/coedit_checkpoint.py``) commits a session's live Yjs
doc to git by rebuilding a throwaway ``Doc`` from its persisted (snapshot,
updates) — it never touches any process's live room, so unlike the OT era
*and* unlike this module's own earlier in-process-only design, checkpointing
now runs as a genuine ``coedit_queue`` task: any worker can dequeue and act
on any session, regardless of which process (if any) currently holds its
live room live.

Three triggers, all just enqueue: a periodic scan (crontab, this queue)
finds every dirty session process-wide and enqueues one checkpoint task
each; explicit save and last-participant-leave (``app/api/coedit.py``,
``app/tasks/coedit_leave.py``) enqueue directly, no longer blocking on an
in-process await.

A checkpoint's result still has to reach any process holding the session's
room live, so it can reconcile its bookkeeping (or reseed, if the committed
content diverged from what the room held — an out-of-band merge folded in
concurrently). Fanned out over the realtime bus exactly like
``coedit_rebase.py``'s own cross-process notify: "which process, if any,
holds this session's room" is the identical resolution problem in both
cases, so this reuses the same shape (``bus.register``/``bus.emit`` +
the durable ``(ydoc_snapshot, coedit_updates)`` pair, so any worker can act on
the bus doesn't echo to the sender).
"""

from __future__ import annotations

import logging

from app.models.coedit import CheckpointResultFrame
from app.tasks.queue import crontab
from app.tasks.queues import coedit_queue
from app.wiki import coedit, coedit_channel
from app.wiki.coedit_checkpoint import checkpoint_session

log = logging.getLogger(__name__)

# Keep git history proportional to editing *sessions*, not keystrokes. The
# live doc's update log in Postgres is the durable "stash"; git commits only
# mark real boundaries, so history stays at ~one commit per session. The
# primary trigger is on-last-leave; these timers are coarse backstops, not
# autosave.
#
# Idle: no *edit* for this long -> flush and commit. Catches a session that
# never formally closes (a tab left open keeps the connection alive, so
# leave never fires) once editing has clearly stopped.
#
# Max-interval: the only timer that commits *mid-session* during genuinely
# continuous editing — a safety valve so agents/readers reading git HEAD
# don't see a multi-hour session's stale content indefinitely. Coarse on
# purpose; a marathon session yields a handful of commits, not a stream.
_IDLE_SECONDS = 300
_MAX_INTERVAL_SECONDS = 900
# Four missed 15-second heartbeats distinguish a departed participant from
# ordinary event-loop lag.
_PARTICIPANT_STALE_SECONDS = 60

@coedit_queue.task()
def checkpoint_coedit_session_task(session_id: int, *, request_id: str | None = None) -> None:
    """Checkpoint one session, notify any live room of the result, then
    close it if everyone has since left and it's now clean.

    ``request_id`` set only for an explicit-save request: acknowledged via
    a broadcast ``CheckpointResultFrame`` (the requesting connection's
    process may not be this task's — there is no cross-process targeted
    reply channel — so every connection in the session sees it and filters
    by ``request_id`` client-side). ``ok`` reflects the task completing
    without raising, same as the old in-process ``await`` semantics — not
    "a commit was actually made" (a clean/no-op session is still ``ok``).

    Re-checks participants *after* checkpointing so a rejoin during the
    commit keeps the session open; closes only if still clean, so a late
    update landing after the checkpoint isn't sealed in a closed session
    (see ``coedit.close_if_clean``).
    """
    ok = False
    try:
        checkpoint_session(session_id)
        ok = True
    finally:
        if request_id is not None:
            coedit_channel.publish_control(
                session_id,
                CheckpointResultFrame(request_id=request_id, ok=ok).model_dump(),
            )
    if not coedit.list_participants(session_id):
        coedit.close_if_clean(session_id)


@coedit_queue.periodic_task(crontab())
def scan_coedit_checkpoints() -> None:
    """One pass of the periodic scan — expire stale participant heartbeats,
    enqueue a checkpoint task for every dirty session process-wide (not just
    this process's own local rooms — a worker can now act on any session
    regardless of where its room, if any, lives), close sessions nothing is
    connected to, and purge closed never-edited sessions.

    Presence is a lease: no process ever deletes a participant from its own
    view of its own sockets, so expiring a lapsed heartbeat here is the only
    thing that removes one."""
    expired = coedit.expire_stale_participants(stale_seconds=_PARTICIPANT_STALE_SECONDS)
    for session_id in expired.changed_session_ids:
        coedit_channel.broadcast_presence(session_id)
    for session_id in expired.empty_session_ids:
        checkpoint_coedit_session_task(session_id)
    if expired.changed_session_ids:
        log.info(
            "coedit presence scan: expired participants in %d session(s)",
            len(expired.changed_session_ids),
        )
    due = coedit.sessions_due_for_checkpoint(
        idle_seconds=_IDLE_SECONDS, max_interval_seconds=_MAX_INTERVAL_SECONDS
    )
    for sess in due:
        checkpoint_coedit_session_task(sess.id)
    if due:
        log.info("coedit checkpoint scan: enqueued %d session(s)", len(due))
    abandoned = coedit.close_abandoned_sessions()
    if abandoned:
        log.info("coedit presence scan: closed %d abandoned session(s)", len(abandoned))
    purged = coedit.purge_viewer_sessions()
    if purged:
        log.info("coedit checkpoint scan: purged %d viewer-only session(s)", purged)
