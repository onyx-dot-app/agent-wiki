"""Checkpoint triggers — when the co-edit checkpoint engine fires.

The engine (``app/wiki/coedit_checkpoint.py``) commits a session's live
Yjs doc to git. This wires up *when* that happens: a periodic scan
checkpoints dirty sessions that have gone idle or are overdue, and the
last-participant-leave and explicit-save paths call the engine directly.

Unlike the OT era, none of this rides ``coedit_queue`` anymore. A session's
live document only exists as one process's in-memory ``coedit_room.Room``
(``pycrdt.Doc`` is thread-affine — see that module), so a checkpoint can
only ever run in the process that holds the room; dispatching it to a
worker via the queue would land on a process with no room to checkpoint
from. Every trigger here is a plain in-process async call instead. The
periodic scan is therefore a per-*web-process* ``asyncio`` task (started
alongside the realtime bus listener in ``app/main.py``'s lifespan, one per
replica, each scanning only its own local rooms) rather than a
queue-registered cron — there is no meaningful "one process across the
deployment" leader for this the way ``TaskQueue.periodic_task`` assumes.
"""

from __future__ import annotations

import asyncio
import logging

from app.wiki import coedit, coedit_room
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
#
# The scan runs every _SCAN_INTERVAL_SECONDS, so both fire "within that long
# of" the threshold.
_IDLE_SECONDS = 300
_MAX_INTERVAL_SECONDS = 900
_SCAN_INTERVAL_SECONDS = 60.0


async def checkpoint_coedit_session(session_id: int) -> None:
    """Commit a session's doc, then close it if everyone has since left.

    Re-checks participants after committing so a rejoin during the commit
    keeps the session open. Closes only if still clean, so a late update
    landing after the checkpoint isn't sealed in a closed session (see
    ``coedit.close_if_clean``)."""
    await checkpoint_session(session_id)
    participants = await asyncio.to_thread(coedit.list_participants, session_id)
    if not participants:
        await asyncio.to_thread(coedit.close_if_clean, session_id)


async def scan_once() -> None:
    """One pass of the periodic scan — checkpoint every locally-rooted
    session that's due, and purge closed never-edited sessions. Public (not
    ``_scan_once``) so it's independently callable/testable; ``_scan_loop``
    is just this run on a timer."""
    due = await asyncio.to_thread(
        coedit.sessions_due_for_checkpoint,
        idle_seconds=_IDLE_SECONDS,
        max_interval_seconds=_MAX_INTERVAL_SECONDS,
    )
    # Only sessions whose room lives in *this* process are ours to act on —
    # a dirty session rooted elsewhere is that process's own scan to pick
    # up (see the module docstring).
    local = [s for s in due if coedit_room.get_room(s.id) is not None]
    for sess in local:
        try:
            await checkpoint_coedit_session(sess.id)
        except Exception:
            log.exception("coedit checkpoint scan: session %s failed", sess.id)
    if local:
        log.info("coedit checkpoint scan: checkpointed %d session(s)", len(local))
    purged = await asyncio.to_thread(coedit.purge_viewer_sessions)
    if purged:
        log.info("coedit checkpoint scan: purged %d viewer-only session(s)", purged)


async def _scan_loop() -> None:
    while True:
        await asyncio.sleep(_SCAN_INTERVAL_SECONDS)
        try:
            await scan_once()
        except Exception:
            log.exception("coedit checkpoint scan: unhandled error")


_scan_task: asyncio.Task[None] | None = None


def start() -> None:
    """Start this process's periodic checkpoint scan. Called once from
    ``app/main.py``'s lifespan, alongside ``bus.start_listener()``."""
    global _scan_task
    _scan_task = asyncio.create_task(_scan_loop())


async def stop() -> None:
    """Cancel the scan loop. Called from the lifespan's shutdown path."""
    global _scan_task
    if _scan_task is None:
        return
    _scan_task.cancel()
    try:
        await _scan_task
    except asyncio.CancelledError:
        pass
    _scan_task = None
