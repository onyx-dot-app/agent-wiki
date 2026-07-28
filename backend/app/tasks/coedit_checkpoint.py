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
``coedit_room.run_on_main_loop``, direct local check plus a bus emit since
the bus doesn't echo to the sender).
"""

from __future__ import annotations

import logging

from app.models.coedit import CheckpointResultFrame, ResyncFrame
from app.realtime import bus
from app.tasks.queue import crontab
from app.tasks.queues import coedit_queue
from app.wiki import coedit, coedit_channel, coedit_room, markdown_yjs
from app.wiki.coedit_checkpoint import CheckpointOutcome, checkpoint_session

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

_CHECKPOINT_LANDED_BUS_KIND = "coedit_checkpoint_landed"


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
        outcome = checkpoint_session(session_id)
        ok = True
        if outcome is not None:
            _notify_checkpoint_landed(outcome)
    finally:
        if request_id is not None:
            coedit_channel.publish_control(
                session_id,
                CheckpointResultFrame(request_id=request_id, ok=ok).model_dump(),
            )
    if not coedit.list_participants(session_id):
        coedit.close_if_clean(session_id)
    # Checked by current status rather than close_if_clean's own return —
    # checkpoint_session itself can also close a session directly (its
    # missing-path guard), bypassing close_if_clean entirely. Notifying
    # again for an already-closed session is harmless (evict_if_local
    # no-ops once the room's gone), so this stays correct even for a
    # duplicate/retried task on a session closed by an earlier attempt.
    sess = coedit.get_session(session_id)
    if sess is not None and sess.status == coedit.SessionStatus.CLOSED.value:
        _notify_session_closed(session_id)


@coedit_queue.periodic_task(crontab())
def scan_coedit_checkpoints() -> None:
    """One pass of the periodic scan — enqueue a checkpoint task for every
    dirty session process-wide (not just this process's own local rooms —
    a worker can now act on any session regardless of where its room, if
    any, lives), and purge closed never-edited sessions."""
    due = coedit.sessions_due_for_checkpoint(
        idle_seconds=_IDLE_SECONDS, max_interval_seconds=_MAX_INTERVAL_SECONDS
    )
    for sess in due:
        checkpoint_coedit_session_task(sess.id)
    if due:
        log.info("coedit checkpoint scan: enqueued %d session(s)", len(due))
    purged = coedit.purge_viewer_sessions()
    if purged:
        log.info("coedit checkpoint scan: purged %d viewer-only session(s)", purged)


def _notify_checkpoint_landed(outcome: CheckpointOutcome) -> None:
    _try_local_reconcile(outcome)
    bus.emit(
        {
            "kind": _CHECKPOINT_LANDED_BUS_KIND,
            "session_id": outcome.session_id,
            "base_sha": outcome.sha,
            "body": outcome.body,
        }
    )


def _try_local_reconcile(outcome: CheckpointOutcome) -> None:
    """Reconcile this process's own room, if it holds one for the session —
    a no-op dict lookup otherwise (cheap enough to call unconditionally
    from every process a checkpoint's fan-out reaches, including the
    checkpointing worker itself, which typically holds no rooms at all)."""
    if coedit_room.get_room(outcome.session_id) is None:
        return
    coedit_room.run_on_main_loop(_reconcile_room(outcome))


async def _reconcile_room(outcome: CheckpointOutcome) -> None:
    # A Doc read — must run inline on this task's own thread (the event
    # loop), not via to_thread; see coedit_room.py.
    room = coedit_room.get_room(outcome.session_id)
    if room is None:
        return  # left/evicted between the schedule and this running
    if markdown_yjs.reconstruct_body(room.doc) == outcome.body:  # type: ignore[reportUnknownMemberType]
        # This room's content is exactly what got committed — nothing to
        # reconcile, just advance the bookkeeping in place.
        room.base_body = outcome.body
        room.base_sha = outcome.sha
        return
    # This room has content the checkpoint didn't see (an edit landed here
    # after the worker read the update log, or the committed result folded
    # in an out-of-band merge this room never had) — reseed from what's
    # actually committed and have connected clients resync. Never loses the
    # divergent edits themselves: anything applied here already bumped
    # ydoc_seq and is durably logged, so it's simply still-dirty relative to
    # the new base and picked up by the next checkpoint.
    coedit_room.reseed(room, outcome.body, outcome.sha)
    coedit_channel.publish_control(
        outcome.session_id, ResyncFrame(session_id=outcome.session_id).model_dump()
    )


def _handle_remote_checkpoint_landed(payload: dict[str, object]) -> None:
    outcome = CheckpointOutcome(
        session_id=int(payload["session_id"]),  # type: ignore[arg-type]
        sha=str(payload["base_sha"]),
        body=str(payload["body"]),
    )
    _try_local_reconcile(outcome)


bus.register(_CHECKPOINT_LANDED_BUS_KIND, _handle_remote_checkpoint_landed)

_SESSION_CLOSED_BUS_KIND = "coedit_session_closed"


def _notify_session_closed(session_id: int) -> None:
    """A session just closed (this task, on last-participant-out) — evict
    its in-memory Room in whichever process (if any) holds it. Without
    this, a closed session's Room (Doc + Awareness + tracker) pins memory
    in its owning web app process forever: nothing else ever calls
    ``coedit_room.close_room``."""
    coedit_room.evict_if_local(session_id)
    bus.emit({"kind": _SESSION_CLOSED_BUS_KIND, "session_id": session_id})


def _handle_remote_session_closed(payload: dict[str, object]) -> None:
    coedit_room.evict_if_local(int(payload["session_id"]))  # type: ignore[arg-type]


bus.register(_SESSION_CLOSED_BUS_KIND, _handle_remote_session_closed)
