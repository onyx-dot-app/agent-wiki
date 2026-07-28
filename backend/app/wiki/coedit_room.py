"""In-process registry of live co-edit "rooms" — one `pycrdt.Doc` +
`Awareness` + `TouchedTracker` per active session, keyed by session id.

Mirrors `coedit_channel.py`'s connection registry (module-level dict + one
lock, in-process, ephemeral) for the same reason: `pycrdt.Doc`/`Subscription`
are PyO3 "unsendable" Rust types (confirmed — `markdown_splice.py`'s own
docstring), so a session's live document can only ever be touched from the
process (and, in practice, the single-threaded-per-room discipline the WS
route observes) that created it. Unlike `coedit_channel`'s connection
registry, a room is **not** shared cross-process via the realtime bus — it
cannot be, since the `Doc` itself is the thing being shared, not a
JSON-serializable frame. A checkpoint trigger (idle scan, last-leave,
explicit save) or a live-rebase (an out-of-band commit landing mid-session)
can only ever act on rooms live in its own process; see
`app/wiki/coedit_checkpoint.py` and `app/wiki/coedit_rebase.py`.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import Coroutine
from typing import Any

from pycrdt import Awareness, Doc

from app.wiki.markdown_splice import TouchedTracker
from app.wiki.markdown_yjs import reconstruct_body, seed_doc_from_markdown

log = logging.getLogger(__name__)


class Room:
    """One session's live document state. Not a pydantic model — `Doc`/
    `Awareness`/`TouchedTracker` are exactly the thread-affine, non-
    serializable objects this module exists to hold, so there's no value in
    a validation layer atop them (see `Connection` in `coedit_channel.py`
    for the pydantic-model version of this pattern, which works there only
    because a `queue.Queue` genuinely is an arbitrary-but-safe-to-share
    type)."""

    __slots__ = ("session_id", "path", "doc", "awareness", "tracker", "base_body", "base_sha")

    session_id: int
    path: str
    doc: Doc
    awareness: Awareness
    tracker: TouchedTracker
    # The markdown text the doc was seeded from, and the git ref it was
    # seeded at — `markdown_splice.checkpoint_body`'s diff base, updated by
    # the checkpoint engine after each successful commit.
    base_body: str
    base_sha: str | None

    def __init__(self, session_id: int, path: str, base_body: str, base_sha: str | None) -> None:
        self.session_id = session_id
        self.path = path
        self.base_body = base_body
        self.base_sha = base_sha
        self.doc = seed_doc_from_markdown(base_body)
        self.awareness = Awareness(self.doc)
        self.tracker = TouchedTracker(self.doc)


_rooms: dict[int, Room] = {}
_lock = threading.Lock()


def get_room(session_id: int) -> Room | None:
    """Thread-safe from any thread: a dict lookup under a lock, returning a
    reference to the ``Room`` (or ``None``) without touching its ``Doc``."""
    with _lock:
        return _rooms.get(session_id)


def create_room(session_id: int, path: str, body: str, base_sha: str | None) -> Room:
    """Construct and register a session's room, seeded from ``body`` (the
    caller has already fetched it — a git read, done separately so it can
    run off the event loop; see ``app/api/coedit.py:_connect_sync``).

    Must be called from the same thread that will go on to touch the room's
    ``Doc``/``Awareness`` afterwards — normally the event loop thread, since
    that's what the WS route's recv/send loops run on. Constructing a
    ``pycrdt.Doc`` binds it to the calling thread (PyO3 "unsendable"), so
    unlike a plain dict insert this cannot be dispatched through
    ``asyncio.to_thread``'s shared worker pool: the pool gives no guarantee
    later calls land back on the same worker thread that ran this one.
    """
    room = Room(session_id, path, body, base_sha)
    with _lock:
        # Lost a race with another connection that reached this point first
        # for the same session — adopt their room, discard ours (never
        # touched by anything, safe to drop).
        existing = _rooms.get(session_id)
        if existing is not None:
            return existing
        _rooms[session_id] = room
        return room


def reseed(room: Room, body: str, base_sha: str | None) -> None:
    """Replace a room's live ``Doc`` wholesale with fresh content — a
    live-rebase fold-in (``coedit_rebase.py``) or a checkpoint's committed
    result landing content this room's ``Doc`` doesn't have
    (``app/tasks/coedit_checkpoint.py``'s cross-process reconcile step).

    Must run on the room's own thread (the event loop — same constraint as
    ``create_room``, since this constructs a brand-new ``Doc``). Existing
    connections' local Yjs replicas are now for a different Doc identity and
    cannot keep applying incremental updates against it; the caller must
    broadcast a resync (``app.models.coedit.ResyncFrame`` via
    ``coedit_channel.publish_control``) so they reconnect and redo the sync
    handshake fresh.
    """
    room.tracker.stop()
    room.doc = seed_doc_from_markdown(body)
    room.awareness = Awareness(room.doc)
    room.tracker = TouchedTracker(room.doc)
    room.base_body = body
    room.base_sha = base_sha


def close_room(session_id: int) -> None:
    """Drop a session's in-memory room — called once nothing local references
    it (after the last connection in this process leaves and a checkpoint,
    if any was due, has run). Idempotent."""
    with _lock:
        _rooms.pop(session_id, None)


async def _evict(session_id: int) -> None:
    room = get_room(session_id)
    if room is None:
        return  # left/evicted between the schedule and this running
    # A Doc-adjacent operation (unsubscribes the tracker's observe_deep
    # callback) — must run inline on this room's own thread, not via
    # to_thread; see the module docstring.
    room.tracker.stop()
    close_room(session_id)


def evict_if_local(session_id: int) -> None:
    """Evict this process's in-memory room for a session that's just
    closed (``app/tasks/coedit_checkpoint.py``, on last-participant-out) —
    a no-op dict lookup if this process holds no room for it, which is the
    common case: rooms only ever live in a web app process (created by
    ``create_room``, called only from the WS route), never a queue
    worker's. Scheduled onto the room's own thread the same way
    ``app/tasks/coedit_checkpoint.py``'s cross-process checkpoint-landed
    notify schedules its own room-touching reconcile step — this is that
    same "which process, if any, holds this session's room" problem again.
    """
    if get_room(session_id) is None:
        return
    run_on_main_loop(_evict(session_id))


def reset_for_tests() -> None:
    """Clear the in-process room registry. Each test gets a fresh Postgres
    database whose ``coedit_sessions`` id sequence restarts at 1 (see
    ``tests/conftest.py``), so without this a room left behind by an earlier
    test (never explicitly ``close_room``'d) would be adopted by a later,
    unrelated test that happens to reuse the same session id — the
    ``tmp_config`` fixture calls this for every test that needs a database,
    same as ``app.mcp_server.session.reset_for_tests`` handles its own
    module-level registry leaking across tests on the same xdist worker."""
    with _lock:
        _rooms.clear()


# --------------------------------------------------------------------------- #
# Cross-thread scheduling — for callers that don't already run on this        #
# process's event loop (the realtime bus listener thread, notably)            #
# --------------------------------------------------------------------------- #

_main_loop: asyncio.AbstractEventLoop | None = None


def bind_main_loop(loop: asyncio.AbstractEventLoop) -> None:
    """Record the event loop rooms live on — call once from this process's
    startup (``app/main.py``'s lifespan), before anything might call
    ``run_on_main_loop``."""
    global _main_loop
    _main_loop = loop


def run_on_main_loop(coro: Coroutine[Any, Any, None]) -> None:
    """Schedule ``coro`` on this process's main event loop from any thread —
    fire and forget. For a realtime-bus handler (live-rebase's fan-out; see
    ``app/tasks/coedit_rebase.py``) that needs to touch a room's ``Doc``:
    the bus listener runs on its own dedicated OS thread
    (``app/realtime/bus.py``), not the event loop, so a handler that found a
    local room can't just await the rebase inline.

    No-ops (closing ``coro`` so it doesn't warn about never being awaited) if
    the loop isn't bound yet — a notify arriving before startup finishes
    binding it, which can only mean nothing local could hold the room yet
    either.
    """
    if _main_loop is None:
        coro.close()
        return
    try:
        asyncio.run_coroutine_threadsafe(coro, _main_loop)
    except RuntimeError:
        # The bound loop is closed (e.g. process shutdown mid-request) —
        # best-effort, same as bus.emit's own swallow-and-log: whatever
        # triggered this has bigger problems than a missed live-rebase.
        log.warning("coedit_room: run_on_main_loop couldn't schedule (loop closed?)")


async def _read_body(room: Room) -> str:
    return reconstruct_body(room.doc)


def read_body_sync(room: Room, *, timeout: float = 2.0) -> str:
    """Reconstruct a room's current markdown body from any thread, blocking
    until the read completes back on the event loop (the only thread
    allowed to touch the ``Doc``).

    For a plain (non-``async def``) HTTP route — FastAPI dispatches those to
    its own worker thread pool, never the event loop, so they can't safely
    touch a ``Doc`` directly the way the WS route does — that wants a live
    room's current content (``app/api/wiki.py``'s session-aware live read).
    Raises ``TimeoutError`` if the loop doesn't respond in time; the caller
    should treat that as "couldn't get the live body" and fall back, not as
    a request-failing error — the read itself is fast, pure in-memory work,
    so a timeout means the loop is unusually busy, not that anything is
    wrong with the room.
    """
    if _main_loop is None:
        raise RuntimeError("coedit_room: main loop not bound yet")
    future = asyncio.run_coroutine_threadsafe(_read_body(room), _main_loop)
    return future.result(timeout=timeout)
