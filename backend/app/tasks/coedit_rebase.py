"""Trigger + cross-process fan-out for live-rebase.

``on_wiki_commit`` is called from ``app.wiki.notify.after_doc_write`` on
every wiki commit, from whatever process/thread did the writing — a web
request, a worker task, anything. If an active session exists for the page
and the commit is external to it, the rebase needs to run wherever that
session's room actually lives (``app/wiki/coedit_room.py`` — a ``pycrdt.Doc``
is thread-affine, so it's never shared cross-process), which this process
generally doesn't know. Resolved the same way the co-edit channel resolves
"which process has this session's connections": fan out over the realtime
bus (``app/realtime/bus.py``) so every process checks its own room registry,
plus a direct local check here (the bus doesn't echo to the sender).

Not a queue task, unlike the OT era's ``lightweight_maintenance_queue``-
dispatched version — a queue worker never holds a room either, so
dispatching there would just relocate the same problem, not solve it.

The engine (``app.wiki.coedit_rebase``) does the merge + doc re-seed.
"""

from __future__ import annotations

import asyncio
import logging

from app.realtime import bus
from app.tasks.coedit_checkpoint import checkpoint_coedit_session_task
from app.wiki import coedit, coedit_room
from app.wiki.coedit_rebase import RebaseOutcome, rebase_session

log = logging.getLogger(__name__)

_BUS_KIND = "coedit_rebase"

async def _rebase_and_maybe_checkpoint(session_id: int, head_sha: str) -> None:
    # No retry loop: folding a commit in is a logged update that commutes with
    # concurrent keystrokes, so there is no race to lose and nothing to retry.
    outcome = await rebase_session(session_id, head_sha)
    if outcome == RebaseOutcome.CONFLICT:
        # Overlap the plain 3-way merge couldn't resolve — enqueue the
        # checkpoint task instead of running the engine inline: it does
        # real DB/git/LLM-merge work that would otherwise block this event
        # loop, and (via checkpoint_coedit_session_task's own cross-process
        # notify) reseeds this room from the result once it lands, the same
        # as the checkpoint engine used to do directly before it stopped
        # touching any room at all.
        await asyncio.to_thread(checkpoint_coedit_session_task, session_id)


def _try_local(session_id: int, head_sha: str) -> None:
    """Fire the rebase if — and only if — this process holds the session's
    room; a no-op dict lookup otherwise (cheap enough to call unconditionally
    from every process a commit's fan-out reaches)."""
    if coedit_room.get_room(session_id) is None:
        return
    coedit_room.run_on_main_loop(_rebase_and_maybe_checkpoint(session_id, head_sha))


def _handle_remote(payload: dict[str, object]) -> None:
    _try_local(int(payload["session_id"]), str(payload["head_sha"]))  # type: ignore[arg-type]


bus.register(_BUS_KIND, _handle_remote)


def on_wiki_commit(rel_path: str, sha: str) -> None:
    """Fan out a live-rebase if an active session exists for ``rel_path`` and
    the commit is external to it (its ``base_sha`` hasn't already advanced to
    ``sha``, which is the case for the session's own checkpoint commit)."""
    sess = coedit.get_active_session(rel_path)
    if sess is None or sess.base_sha == sha:
        return
    _try_local(sess.id, sha)
    bus.emit({"kind": _BUS_KIND, "session_id": sess.id, "head_sha": sha})
