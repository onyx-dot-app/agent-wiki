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

import asyncio
import logging

from app.models.coedit import CheckpointResultFrame, ResyncFrame
from app.realtime import bus
from app.tasks.queue import crontab
from app.tasks.queues import coedit_queue
from app.wiki import coedit, coedit_channel, coedit_room, markdown_splice
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
    """Fan out that a checkpoint landed for ``outcome.session_id`` — the bus
    payload deliberately carries only ``session_id``/``diverged``, not the
    committed body: an earlier version shipped the whole page body through
    ``bus.emit``, which is a Postgres NOTIFY capped at
    ``bus.MAX_PAYLOAD_BYTES`` (8000) — measured against Postgres directly,
    a ~7.7KB body already exceeds it (``InvalidParameterValue: payload
    string too long``), silently swallowed by ``bus.emit``'s own
    generic-exception log, so most real pages never actually reconciled
    another process's room at all (confirmed in review). ``_reconcile_room``
    re-reads the committed body fresh from the DB instead — already
    durably there by the time this notify fires (``advance_checkpoint``
    ran inside ``checkpoint_session``, before it returned this outcome) —
    which has no size concern and, as a bonus, always reconciles against
    whatever the *latest* known-good state is even if another checkpoint
    landed in the gap between this one committing and this notify actually
    running.
    """
    _try_local_reconcile(outcome.session_id, outcome.diverged)
    bus.emit(
        {
            "kind": _CHECKPOINT_LANDED_BUS_KIND,
            "session_id": outcome.session_id,
            "diverged": outcome.diverged,
        }
    )


def _try_local_reconcile(session_id: int, diverged: bool) -> None:
    """Reconcile this process's own room, if it holds one for the session —
    a no-op dict lookup otherwise (cheap enough to call unconditionally
    from every process a checkpoint's fan-out reaches, including the
    checkpointing worker itself, which typically holds no rooms at all)."""
    if coedit_room.get_room(session_id) is None:
        return
    coedit_room.run_on_main_loop(_reconcile_room(session_id, diverged))


async def _reconcile_room(session_id: int, diverged: bool) -> None:
    room = coedit_room.get_room(session_id)
    if room is None:
        return  # left/evicted between the schedule and this running
    # Always re-read fresh from the DB rather than trusting values carried
    # on the caller's own outcome — see _notify_checkpoint_landed's
    # docstring for why (no cross-process payload-size concern, and always
    # reflects the latest committed state even if a newer checkpoint has
    # already landed in the meantime).
    sess = await asyncio.to_thread(coedit.get_session_for_checkpoint, session_id)
    if sess is None:
        return  # gone
    if not diverged:
        # This room's own doc already reflects exactly what got committed
        # — checkpointing never touches it, so there's nothing to reseed
        # here regardless of what a full reconstruct_body reserialize of
        # it would look like (deliberately not compared: reconstruct_body
        # and checkpoint_body's own verbatim-slice output diverge for tight
        # lists, task lists, ordered lists, and emphasis runs even when the
        # *content* is identical, which used to force a needless reseed —
        # see review). Still restamp block/row ids to match the committed
        # body's own (freshly re-derived every parse) positional
        # numbering: this doc is kept as-is rather than reseeded, so its
        # ids would otherwise silently drift out of sync the moment a
        # future checkpoint's base_body has a different block count/order
        # — see restamp_block_ids's own docstring.
        markdown_splice.restamp_block_ids(room.doc, sess.ydoc_snapshot_body)  # type: ignore[reportUnknownMemberType]
        room.base_body = sess.ydoc_snapshot_body
        room.base_sha = sess.base_sha
        return
    # An out-of-band merge folded in content this room's doc never had —
    # reseed from the just-persisted snapshot and have connected clients
    # resync. Reseeding from an independent seed_doc_from_markdown(body)
    # call here instead of the persisted ydoc_snapshot bytes would be
    # wrong: two separate seedings of "the same" text produce incompatible
    # CRDT lineages (see coedit_room.reseed). The persisted snapshot's own
    # lineage is now spliced from this session's existing doc rather than
    # freshly seeded (coedit_checkpoint.checkpoint_session's
    # apply_markdown_diff call, see its docstring) specifically so this
    # reseed doesn't erase a concurrent edit — but the snapshot only
    # reflects state as of ydoc_snapshot_seq; an edit logged *after* the
    # checkpoint captured that seq (this room's own doc, still live and
    # accepting edits the whole time — checkpointing never touches it) is
    # durably logged but not yet folded into the snapshot bytes themselves.
    # Replay it now, the same way a fresh checkpoint's _rebuild_doc would —
    # this was the actual gap a prior version of this comment claimed
    # didn't exist ("never loses the divergent edits ... picked up by the
    # next checkpoint" — false: the reseed below replaced this room's doc
    # outright, and nothing replayed what the new one was missing, so nothing
    # ever *did* pick it up; confirmed via review repro).
    if sess.ydoc_snapshot is None:
        return  # racing a close — nothing to reconcile
    coedit_room.reseed(room, sess.ydoc_snapshot, sess.ydoc_snapshot_body, sess.base_sha)
    late = await asyncio.to_thread(coedit.updates_since, session_id, sess.ydoc_snapshot_seq)
    for u in late.updates:
        try:
            room.doc.apply_update(u.update_payload)  # type: ignore[reportUnknownMemberType]
        except Exception:
            log.exception(
                "coedit checkpoint: session %s seq %d update failed to apply during"
                " reconcile replay; skipping",
                session_id,
                u.seq,
            )
    coedit_channel.publish_control(session_id, ResyncFrame(session_id=session_id).model_dump())


def _handle_remote_checkpoint_landed(payload: dict[str, object]) -> None:
    _try_local_reconcile(int(payload["session_id"]), bool(payload["diverged"]))  # type: ignore[arg-type]


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
