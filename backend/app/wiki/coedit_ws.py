"""In-memory Yjs room registry for the live co-edit doc. One
``pycrdt.websocket`` ``YRoom`` per active co-edit session, keyed by
wiki-relative path (matching "at most one active session per page").

Room lifecycle:

- First connect to a path with no in-memory room in *this* process: build
  (or rebuild) the live ``Doc`` from Postgres (``ydoc_snapshot`` + any
  ``coedit_updates`` since it, or a fresh seed from git HEAD if no session
  has used this page yet), attach a ``TouchedTracker`` and a persistence
  observer, then hand it to ``pycrdt.websocket``'s ``WebsocketServer``.
- Every applied update (local or remote) is logged to ``coedit_updates``
  through the persistence observer — durability + the catch-up path for a
  process that doesn't already hold the room in memory. It is *not* how
  same-process peers see each other's edits — that's pycrdt's own Yjs sync
  protocol, native to ``room.serve()``.
- Checkpointing (splice + git commit) is
  ``coedit_checkpoint.checkpoint_ydoc_session`` — see that module's note on
  why it must run in *this* process rather than on a generic worker.

Single-process rooms: live edits only fan out to clients connected to *this*
app process, not across the whole deployment — the same constraint the
prior SSE-based transport already had, and not a concern in practice since
``backend.replicaCount`` is pinned to 1 (see ``deploy/README.md``) for
unrelated reasons (the wiki-data RWO volume, the in-process cron scheduler),
so there is only ever one process that *could* hold a room at all.

The one place that constraint still bites is cross-*role* (not
cross-*replica*): an external commit (agent/ingest/plain write) lands on a
``worker-light`` process — a different process from the one backend replica
that actually holds the room — via ``app/tasks/coedit_rebase.py``. That
module reaches this one through the realtime bus (``app/realtime/bus.py``,
Postgres LISTEN/NOTIFY — the same mechanism the prior SSE transport used for
its own cross-process delivery), not the task queue: ``on_checkpoint_needed``
below is registered against a ``coedit_checkpoint_needed`` payload kind, and
if this process holds the named path's room, it schedules an immediate
checkpoint via ``run_coroutine_threadsafe`` (bus handlers run on the bus's
own listener *thread*, not the event loop, and pycrdt's ``Doc`` is
thread-affine — the checkpoint must run on the loop that owns the room).
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

from app.models.coedit import LiveAnchor
from app.realtime import bus
from app.wiki import comment_anchor, coedit
from app.wiki import git as wiki_git
from app.wiki.markdown_splice import TouchedTracker
from app.wiki.markdown_yjs import (
    BlockSpan,
    reconstruct_body,
    reconstruct_body_with_block_map,
    seed_doc_from_markdown,
)

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
# path -> user_id -> open-connection count, for the multi-tab leave guard
# (a second tab closing shouldn't evict the participant row). Presence
# itself has no server-tracked equivalent here — the frontend reads it
# straight off Yjs Awareness (peer-to-peer), unlike the prior SSE
# transport's server-pushed presence frames.
_connected_users: dict[str, dict[str, int]] = {}
_lock = threading.Lock()

# Update persistence runs on one dedicated thread rather than the coedit_queue
# task infrastructure: a single Postgres INSERT is well under the ~100ms
# threshold CLAUDE.md's "queue it" rule targets, and routing every keystroke's
# update through Redis Streams would add per-update network round-trips to a
# hot path for no correctness benefit — durability only needs updates to land
# in commit order, which one consumer thread guarantees.
_persist_queue: queue.Queue[tuple[int, bytes] | None] = queue.Queue()
_persist_thread: threading.Thread | None = None
_scan_task: asyncio.Task[None] | None = None
# Captured in start() (called from the lifespan's async context) so the bus
# listener thread — not the event loop — can marshal a checkpoint back onto
# the loop that owns the room via run_coroutine_threadsafe.
_loop: asyncio.AbstractEventLoop | None = None


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
    """Start the persistence worker thread and the checkpoint scan loop, and
    register this process's live-rebase checkpoint handler on the realtime
    bus — called once from the app lifespan, alongside ``SERVER``'s own
    ``async with`` startup and ``bus.start_listener()``."""
    global _persist_thread, _scan_task, _loop
    _loop = asyncio.get_running_loop()
    if _persist_thread is None:
        _persist_thread = threading.Thread(
            target=_persist_worker, name="coedit-ws-persist", daemon=True
        )
        _persist_thread.start()
    if _scan_task is None:
        _scan_task = asyncio.create_task(_scan_loop())
    bus.register("coedit_checkpoint_needed", _on_checkpoint_needed)


def stop() -> None:
    global _scan_task
    _persist_queue.put(None)
    if _scan_task is not None:
        _scan_task.cancel()
        _scan_task = None


def _on_checkpoint_needed(payload: dict[str, object]) -> None:
    """Bus handler for a ``coedit_checkpoint_needed`` NOTIFY — an external
    commit landed on a page (``app/tasks/coedit_rebase.py``) and needs
    reconciling into that page's live doc, if this process happens to hold
    it. Runs on the bus's listener *thread* (see ``bus.register``'s
    docstring); the actual checkpoint must run on the event loop that owns
    the room (pycrdt's ``Doc`` is thread-affine), so this only decides
    locally-held-or-not and marshals the real work across via
    ``run_coroutine_threadsafe``. A path not held here is silently ignored —
    it isn't (and can't be) held by more than one process at a time given
    ``backend.replicaCount: 1`` (see module docstring), so no other handler
    needs to see it either."""
    path = payload.get("path")
    if not isinstance(path, str):
        return
    if SERVER.rooms.get(path) is None or tracker_for(path) is None:
        return
    if _loop is None:
        log.warning("coedit_ws: checkpoint-needed for %r before start() ran; dropping", path)
        return
    asyncio.run_coroutine_threadsafe(_reconcile_external_commit(path), _loop)


async def _reconcile_external_commit(path: str) -> None:
    from app.wiki.coedit_checkpoint import checkpoint_ydoc_session

    session_id = session_id_for(path)
    tracker = tracker_for(path)
    room = SERVER.rooms.get(path)
    if session_id is None or tracker is None or room is None:
        return  # dropped between the handler's check and this running
    try:
        await checkpoint_ydoc_session(
            session_id, doc=room.ydoc, tracker=tracker, author_user_id=None
        )
    except Exception:
        log.exception("coedit_ws: live-rebase checkpoint failed for %r", path)


def tracker_for(path: str) -> TouchedTracker | None:
    with _lock:
        return _trackers.get(path)


def session_id_for(path: str) -> int | None:
    with _lock:
        return _session_ids.get(path)


def connect_user(path: str, user_id: str) -> None:
    """Register one more open connection for ``user_id`` on ``path`` — call
    right after a WS connection is accepted."""
    with _lock:
        counts = _connected_users.setdefault(path, {})
        counts[user_id] = counts.get(user_id, 0) + 1


def disconnect_user(path: str, user_id: str) -> None:
    """Undo ``connect_user`` — call before ``record_leave`` in teardown so
    ``user_still_connected`` reflects this connection as already gone."""
    with _lock:
        counts = _connected_users.get(path)
        if counts is None:
            return
        remaining = counts.get(user_id, 1) - 1
        if remaining <= 0:
            counts.pop(user_id, None)
        else:
            counts[user_id] = remaining
        if not counts:
            _connected_users.pop(path, None)


def user_still_connected(path: str, user_id: str) -> bool:
    """True if ``user_id`` has any other open connection to ``path`` in this
    process — lets the caller avoid firing a leave when one of a user's
    several tabs closes while another stays open."""
    with _lock:
        return _connected_users.get(path, {}).get(user_id, 0) > 0


def _build_doc(path: str, session_id: int) -> tuple[Doc, bool]:
    """Runs off the event loop (blocking DB/git reads). Returns ``(doc,
    rebuilt_from_snapshot)`` — the latter tells the caller whether the fresh
    ``TouchedTracker`` needs ``mark_all_touched()`` (see that method's
    docstring: a snapshot-rebuilt doc has no reliable touched-region history).

    Always replays every ``coedit_updates`` row since the baseline (the
    snapshot's own checkpointed seq, or seq 0 when there's no snapshot yet)
    — updates are persisted on every applied change regardless of whether a
    checkpoint has ever run, so a session with live edits but no checkpoint
    yet still needs its update log replayed on top of the fresh git-seeded
    doc, not just the seed alone.
    """
    state = coedit.get_ydoc_state(session_id)
    if state is not None and state.snapshot is not None:
        doc = Doc()
        doc.apply_update(state.snapshot)
        since = state.checkpointed_seq
        rebuilt_from_snapshot = True
    else:
        base_sha = state.base_sha if state is not None else None
        base_body = wiki_git.read_file_opt(path, ref=base_sha) if base_sha else None
        doc = seed_doc_from_markdown(base_body or "")
        since = 0
        rebuilt_from_snapshot = False
    for update_bytes in coedit.ydoc_updates_since(session_id, since):
        doc.apply_update(update_bytes)
    return doc, rebuilt_from_snapshot


def reconstruct_live_body(session_id: int, path: str) -> str:
    """Best-effort reconstruction of a session's current live markdown body
    from Postgres alone — no live in-memory room required (or trusted, even
    if this process happens to hold one; always rebuilds fresh from the
    persisted snapshot + update log for a single, simple code path). Used
    for display-only reads (``GET /api/wiki/document``'s session-aware
    path) where "not necessarily byte-identical, but correct" is an
    accepted tradeoff — same as ``markdown_yjs.reconstruct_body``'s general
    contract, which this calls directly.
    """
    doc, _ = _build_doc(path, session_id)
    return reconstruct_body(doc)


def resolve_live_spans(
    session_id: int, path: str, anchor_sha: str, spans: list[tuple[int, int]]
) -> list[tuple[LiveAnchor, LiveAnchor] | None]:
    """Re-anchor a batch of ``[start, end)`` spans — already current as of
    ``anchor_sha`` (comments/sources are kept remapped to HEAD on every
    commit by ``app/wiki/anchor_remap.py``, so this is normally HEAD) — onto
    the session's live, not-yet-committed doc. One ``(start, end)``
    ``LiveAnchor`` pair per input span, in the same order; ``None`` for a
    span ``comment_anchor.remap_range`` decides is orphaned by whatever's
    changed since ``anchor_sha`` (see that module — a genuine rewrite, not
    just live drift, should still orphan rather than silently mis-anchor).

    Batches the (relatively expensive) live-doc reconstruction once for the
    whole page rather than per span — a page's comments/sources are always
    resolved against the same live doc in one call.
    """
    doc, _ = _build_doc(path, session_id)
    live_body, block_spans = reconstruct_body_with_block_map(doc)
    old_body = wiki_git.read_file_opt(path, ref=anchor_sha) or ""

    results: list[tuple[LiveAnchor, LiveAnchor] | None] = []
    for start, end in spans:
        remapped = comment_anchor.remap_range(old_body, live_body, start, end)
        if remapped is None:
            results.append(None)
            continue
        new_start, new_end = remapped
        start_anchor = _offset_to_block_position(block_spans, new_start)
        end_anchor = _offset_to_block_position(block_spans, new_end)
        if start_anchor is None or end_anchor is None:
            results.append(None)
            continue
        results.append((start_anchor, end_anchor))
    return results


def _offset_to_block_position(spans: list[BlockSpan], offset: int) -> LiveAnchor | None:
    for span in spans:
        if span.start <= offset <= span.end:
            return LiveAnchor(block_id=span.block_id, block_offset=offset - span.start)
    return None


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


# Same cutoffs as the checkpoint task's periodic scan — see that module's
# comments for the idle-vs-overdue rationale.
_IDLE_SECONDS = 300
_MAX_INTERVAL_SECONDS = 900
_SCAN_INTERVAL_SECONDS = 30


async def _scan_and_checkpoint_local_rooms() -> None:
    """Checkpoint this process's own dirty, idle-or-overdue rooms.

    A Yjs session can only be checkpointed by the process holding its live
    doc — see ``coedit_checkpoint``'s module note. So this filters the
    global due-list down to sessions whose room this process actually has,
    and lets whichever process(es) hold the rest handle them on their own
    scan.
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
