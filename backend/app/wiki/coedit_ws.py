"""In-memory Yjs room registry for the onyx-editor live doc (Phase 1,
``plans/onyx-editor.md``) — additive alongside the SSE+POST transport in
``app/api/coedit.py`` until Phase 2/3 cut the frontend over. One
``pycrdt.websocket`` ``YRoom`` per active co-edit session, keyed by
wiki-relative path (matching "at most one active session per page").

Room lifecycle:

- First connect to a path with no in-memory room in *this* process: build
  (or rebuild) the live ``Doc`` from Postgres (``ydoc_snapshot`` + any
  ``coedit_updates`` since it, or a fresh seed from git HEAD if no session
  has used the WS path for this page yet), attach a ``TouchedTracker`` and a
  persistence observer, then hand it to ``pycrdt.websocket``'s
  ``WebsocketServer``.
- Every applied update (local or remote) is logged to ``coedit_updates``
  through the persistence observer — durability + the catch-up path for a
  process that doesn't already hold the room in memory. It is *not* how
  same-process peers see each other's edits — that's pycrdt's own Yjs sync
  protocol, native to ``room.serve()``.
- Checkpointing (splice + git commit) is ``coedit_checkpoint.checkpoint_ydoc_session``
  — see that module's note on why it must run in *this* process rather than
  on the ``coedit_queue`` worker.

Single-process rooms: like the existing SSE broadcast (``coedit_channel.py``),
this only fans updates out to clients connected to *this* app process — not a
new limitation, the same constraint the current transport already has.
"""

from __future__ import annotations

import asyncio
import logging
import queue
import threading

import anyio
from pycrdt import Doc, Subscription
from pycrdt.websocket import WebsocketServer
from pycrdt.websocket.yroom import YRoom

from app.wiki import coedit
from app.wiki import git as wiki_git
from app.wiki.markdown_splice import TouchedTracker
from app.wiki.markdown_yjs import seed_doc_from_markdown

log = logging.getLogger(__name__)

SERVER = WebsocketServer(rooms_ready=True, auto_clean_rooms=True, log=log)

# In-memory registries, one entry per room this process currently holds live
# (see module docstring — not shared across processes). Guarded by _lock
# since pycrdt's observer callbacks and FastAPI's async route handlers can
# both touch these.
_trackers: dict[str, TouchedTracker] = {}
_session_ids: dict[str, int] = {}
# The persistence Doc.observe() Subscription, held here for the room's
# lifetime. pycrdt's Subscription is a thread-affine ("unsendable") Rust
# type — an unreferenced one gets garbage-collected at an unpredictable
# point, possibly from a different thread than it was created on, which
# crashes. Must be explicitly unsubscribed in drop_room, same as the
# tracker's own subscription (TouchedTracker.stop()).
_persist_subscriptions: dict[str, Subscription] = {}
_lock = threading.Lock()

# Update persistence runs on one dedicated thread (mirroring coedit_channel.py's
# use of a plain threading.Queue for in-process realtime plumbing) rather than
# the coedit_queue task infrastructure: a single Postgres INSERT is well under
# the ~100ms threshold CLAUDE.md's "queue it" rule targets, and routing every
# keystroke's update through Redis Streams would add per-update network
# round-trips to a hot path for no correctness benefit — durability only needs
# updates to land in commit order, which one consumer thread guarantees.
_persist_queue: queue.Queue[tuple[int, bytes] | None] = queue.Queue()
_persist_thread: threading.Thread | None = None
_scan_task: asyncio.Task[None] | None = None


def _persist_worker() -> None:
    while True:
        item = _persist_queue.get()
        if item is None:  # stop() sentinel
            return
        session_id, update_bytes = item
        try:
            coedit.append_ydoc_update(session_id, update_bytes=update_bytes, author_user_id=None)
        except Exception:
            log.exception("coedit_ws: failed to persist update for session %s", session_id)


def start() -> None:
    """Start the persistence worker thread and the checkpoint scan loop —
    called once from the app lifespan, alongside ``SERVER``'s own
    ``async with`` startup."""
    global _persist_thread, _scan_task
    if _persist_thread is None:
        _persist_thread = threading.Thread(
            target=_persist_worker, name="coedit-ws-persist", daemon=True
        )
        _persist_thread.start()
    if _scan_task is None:
        _scan_task = asyncio.create_task(_scan_loop())


def stop() -> None:
    global _scan_task
    _persist_queue.put(None)
    if _scan_task is not None:
        _scan_task.cancel()
        _scan_task = None


def tracker_for(path: str) -> TouchedTracker | None:
    with _lock:
        return _trackers.get(path)


def session_id_for(path: str) -> int | None:
    with _lock:
        return _session_ids.get(path)


def _build_doc(path: str, session_id: int) -> tuple[Doc, bool]:
    """Runs off the event loop (blocking DB/git reads). Returns ``(doc,
    rebuilt_from_snapshot)`` — the latter tells the caller whether the fresh
    ``TouchedTracker`` needs ``mark_all_touched()`` (see that method's
    docstring: a snapshot-rebuilt doc has no reliable touched-region history)."""
    state = coedit.get_ydoc_state(session_id)
    if state is not None and state.snapshot is not None:
        doc = Doc()
        doc.apply_update(state.snapshot)
        for update_bytes in coedit.ydoc_updates_since(session_id, state.checkpointed_seq):
            doc.apply_update(update_bytes)
        return doc, True

    base_sha = state.base_sha if state is not None else None
    base_body = wiki_git.read_file_opt(path, ref=base_sha) if base_sha else None
    return seed_doc_from_markdown(base_body or ""), False


async def get_or_create_room(path: str, *, session_id: int) -> YRoom:
    """The live room for ``path`` in this process, building it if needed."""
    existing = SERVER.rooms.get(path)
    if existing is not None:
        return existing

    doc, rebuilt_from_snapshot = await anyio.to_thread.run_sync(_build_doc, path, session_id)

    tracker = TouchedTracker(doc)
    if rebuilt_from_snapshot:
        tracker.mark_all_touched()

    persist_sub = doc.observe(lambda event: _persist_queue.put((session_id, bytes(event.update))))

    room = YRoom(ydoc=doc, ready=True, log=log)
    with _lock:
        _trackers[path] = tracker
        _session_ids[path] = session_id
        _persist_subscriptions[path] = persist_sub
    SERVER.rooms[path] = room
    await SERVER.start_room(room)
    return room


# Same cutoffs as app/tasks/coedit_checkpoint.py's buffer_text scan — see that
# module's comments for the idle-vs-overdue rationale.
_IDLE_SECONDS = 300
_MAX_INTERVAL_SECONDS = 900
_SCAN_INTERVAL_SECONDS = 30


async def _scan_and_checkpoint_local_rooms() -> None:
    """Checkpoint this process's own dirty, idle-or-overdue rooms.

    Unlike ``app/tasks/coedit_checkpoint.py``'s ``scan_and_checkpoint`` (which
    any worker can act on, because ``buffer_text`` lives in Postgres), a Yjs
    session can only be checkpointed by the process holding its live doc — see
    ``coedit_checkpoint``'s module note. So this filters the global due-list
    down to sessions whose room this process actually has, and lets whichever
    process(es) hold the rest handle them on their own scan.
    """
    from app.wiki.coedit_checkpoint import checkpoint_ydoc_session

    due = coedit.sessions_due_for_ydoc_checkpoint(
        idle_seconds=_IDLE_SECONDS, max_interval_seconds=_MAX_INTERVAL_SECONDS
    )
    for sess in due:
        tracker = tracker_for(sess.path)
        room = SERVER.rooms.get(sess.path)
        if tracker is None or room is None:
            continue  # not held by this process — another one's scan handles it
        try:
            # Not thread-offloaded: pycrdt's Doc/Subscription objects are
            # thread-affine (see coedit_checkpoint's module note).
            # checkpoint_ydoc_session offloads only its pure-string git-commit
            # step internally.
            await checkpoint_ydoc_session(
                sess.id, doc=room.ydoc, tracker=tracker, author_user_id=None
            )
        except Exception:
            log.exception("coedit_ws: checkpoint scan failed for session %s", sess.id)


async def _scan_loop() -> None:
    while True:
        await anyio.sleep(_SCAN_INTERVAL_SECONDS)
        await _scan_and_checkpoint_local_rooms()


def drop_room(path: str) -> None:
    """Forget this process's in-memory room/tracker for ``path``.
    ``auto_clean_rooms`` already drops the ``YRoom`` itself from
    ``SERVER.rooms`` once it has no clients; this clears our parallel
    registries so a later reconnect rebuilds fresh from Postgres rather than
    reusing a stale tracker."""
    with _lock:
        tracker = _trackers.pop(path, None)
        _session_ids.pop(path, None)
        persist_sub = _persist_subscriptions.pop(path, None)
    if tracker is not None:
        tracker.stop()
    if persist_sub is not None:
        persist_sub.drop()
