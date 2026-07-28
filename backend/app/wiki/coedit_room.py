"""In-process registry of live co-edit "rooms" — one `pycrdt.Doc` +
`Awareness` + `TouchedTracker` per active session, keyed by session id.

Mirrors `coedit_channel.py`'s connection registry (module-level dict + one
lock, in-process, ephemeral) for the same reason: `pycrdt.Doc`/`Subscription`
are PyO3 "unsendable" Rust types (confirmed — `markdown_splice.py`'s own
docstring), so a session's live document can only ever be touched from the
process (and, in practice, the single-threaded-per-room discipline the WS
route observes) that created it. Unlike `coedit_channel`'s connection
registry, a room's `Doc` object itself is **not** shared cross-process via
the realtime bus — it cannot be, it's not a JSON-serializable frame. Its
*content* does converge across processes, though: every raw Yjs frame
`coedit_channel.broadcast_yjs` relays cross-process is also applied to
this process's own local room, if it holds one, via
`apply_remote_frame_if_local` — two rooms in different processes stay in
sync on an ongoing basis, not just at the moment each was created
(rehydrating from `(ydoc_snapshot, coedit_updates)` only fixes CRDT
lineage *once*, at that moment). A checkpoint trigger (idle scan,
last-leave, explicit save) or a live-rebase (an out-of-band commit
landing mid-session) still can only ever act on rooms live in its own
process, though; see `app/wiki/coedit_checkpoint.py` and
`app/wiki/coedit_rebase.py`.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import Coroutine
from typing import Any

from pycrdt import Awareness, Doc, YMessageType, handle_sync_message, read_message

from app.wiki.markdown_splice import TouchedTracker
from app.wiki.markdown_yjs import reconstruct_body

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

    def __init__(
        self, session_id: int, path: str, doc: Doc, base_body: str, base_sha: str | None
    ) -> None:
        self.session_id = session_id
        self.path = path
        self.base_body = base_body
        self.base_sha = base_sha
        self.doc = doc
        self.awareness = Awareness(self.doc)
        self.tracker = TouchedTracker(self.doc)


_rooms: dict[int, Room] = {}
_lock = threading.Lock()


def get_room(session_id: int) -> Room | None:
    """Thread-safe from any thread: a dict lookup under a lock, returning a
    reference to the ``Room`` (or ``None``) without touching its ``Doc``."""
    with _lock:
        return _rooms.get(session_id)


def create_room(session_id: int, path: str, doc: Doc, base_body: str, base_sha: str | None) -> Room:
    """Construct and register a session's room around ``doc`` — already
    built by the caller (either fresh via ``seed_doc_from_markdown`` for a
    session with no durable Yjs state yet, or rehydrated from
    ``(ydoc_snapshot, coedit_updates)`` for one that already has some —
    see ``app/api/coedit.py:ws``, which picks between the two. ``base_body``
    must be exactly what ``doc`` decodes to, either way.

    Must be called from the same thread that will go on to touch the room's
    ``Doc``/``Awareness`` afterwards — normally the event loop thread, since
    that's what the WS route's recv/send loops run on. Constructing (or
    calling ``apply_update`` on) a ``pycrdt.Doc`` binds it to the calling
    thread (PyO3 "unsendable"), so unlike a plain dict insert this cannot be
    dispatched through ``asyncio.to_thread``'s shared worker pool: the pool
    gives no guarantee later calls land back on the same worker thread that
    ran this one.
    """
    room = Room(session_id, path, doc, base_body, base_sha)
    with _lock:
        # Lost a race with another connection that reached this point first
        # for the same session — adopt their room, discard ours (never
        # touched by anything, safe to drop).
        existing = _rooms.get(session_id)
        if existing is not None:
            return existing
        _rooms[session_id] = room
        return room


def reseed(room: Room, snapshot: bytes, body: str, base_sha: str | None) -> None:
    """Replace a room's live ``Doc`` wholesale with fresh content — a
    live-rebase fold-in (``coedit_rebase.py``) or a checkpoint's committed
    result landing content this room's ``Doc`` doesn't have
    (``app/tasks/coedit_checkpoint.py``'s cross-process reconcile step).

    Reconstructs the new ``Doc`` by applying ``snapshot`` (the caller's own
    already-built ``Doc.get_update()`` bytes for ``body``), never by an
    independent ``seed_doc_from_markdown(body)`` call here — two separate
    calls seeding "the same" text produce *incompatible* CRDT lineages
    (pycrdt assigns a fresh random client id per ``Doc()``), so a caller
    that persists a snapshot from one call and reseeds the live room from
    another silently breaks: an update logged against this room's post-
    reseed lineage can fail to integrate when a future checkpoint replays
    it onto the *persisted* (differently-seeded) snapshot (caught in
    review). Applying an update instead of seeding fresh reproduces the
    exact same lineage the snapshot itself belongs to.

    Must run on the room's own thread (the event loop — same constraint as
    ``create_room``, since this constructs a brand-new ``Doc``). Existing
    connections' local Yjs replicas are now for a different Doc identity and
    cannot keep applying incremental updates against it; the caller must
    broadcast a resync (``app.models.coedit.ResyncFrame`` via
    ``coedit_channel.publish_control``) so they reconnect and redo the sync
    handshake fresh.
    """
    room.tracker.stop()
    room.doc = Doc()
    room.doc.apply_update(snapshot)
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


async def _apply_remote_frame(session_id: int, raw: bytes) -> None:
    room = get_room(session_id)
    if room is None:
        return  # evicted between the schedule and this running
    if not raw:
        return
    msg_type = raw[0]
    if msg_type == YMessageType.SYNC:
        inner = raw[1:]
        if not inner:
            return
        # handle_sync_message applies SYNC_STEP2/SYNC_UPDATE content to
        # room.doc as a side effect and returns a reply only for a
        # SYNC_STEP1 query — never the case here, see apply_remote_frame_
        # if_local's own docstring — so the return value has nowhere to go
        # and is discarded.
        handle_sync_message(inner, room.doc)  # type: ignore[reportUnknownMemberType]
    elif msg_type == YMessageType.AWARENESS:
        payload = read_message(raw[1:])
        room.awareness.apply_awareness_update(payload, "remote")  # type: ignore[reportUnknownMemberType]


def apply_remote_frame_if_local(session_id: int, raw: bytes) -> None:
    """Apply a raw Yjs sync/awareness frame that arrived from *another*
    process's connection (via ``coedit_channel``'s realtime-bus relay) to
    this process's own local room, if it holds one for the session — a
    no-op dict lookup otherwise (the common case).

    Without this, rehydrating a room from ``(ydoc_snapshot, coedit_updates)``
    only fixes CRDT lineage *at creation time*: two rooms in different
    processes, once both exist, never see each other's edits again — this
    process's ``room.doc`` just freezes at whatever it was seeded with,
    forever, with respect to the other process's connections (confirmed in
    review: with ``--workers 2``, the deployed default, ``read_body_sync``
    serves stale content and a client joining via this process misses the
    other side's blocks entirely, until a checkpoint-driven resync). This
    closes that gap: every raw frame ``coedit_channel.broadcast_yjs``
    fans out cross-process also gets applied here, so both processes'
    ``Doc``s converge continuously, not just once at room creation.

    Never re-broadcasts or re-logs the update — the originating process's
    own ``_apply_yjs_frame`` (``app/api/coedit.py``) already did both;
    this only keeps *this* process's in-memory ``Doc``/``Awareness``
    converged with it. The frame is always content (``SYNC_STEP2``/
    ``SYNC_UPDATE``) or ``AWARENESS`` — ``broadcast_yjs`` is never called
    for a bare ``SYNC_STEP1`` query (see ``_apply_yjs_frame``), so there's
    never a reply to send anywhere from here.

    Scheduled onto the room's own thread (the event loop) the same way
    every other cross-process room-touching notify in this codebase is —
    see ``run_on_main_loop``.
    """
    if get_room(session_id) is None:
        return
    run_on_main_loop(_apply_remote_frame(session_id, raw))


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
        # run_coroutine_threadsafe raises before it ever schedules `coro`
        # in this case, so it's otherwise never awaited — same leak
        # (RuntimeWarning: coroutine never awaited) the `_main_loop is
        # None` branch above already guards against; close it here too.
        coro.close()
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
    Raises ``TimeoutError`` if the loop doesn't respond in time, or the
    bound loop is already closed (e.g. a room outlives the connection that
    registered it, as can happen with a test client that runs each
    connection on its own short-lived loop) — the caller should treat that
    as "couldn't get the live body" and fall back, not as a request-failing
    error. A live timeout means the loop is unusually busy, not that
    anything is wrong with the room.
    """
    if _main_loop is None:
        raise RuntimeError("coedit_room: main loop not bound yet")
    coro = _read_body(room)
    try:
        future = asyncio.run_coroutine_threadsafe(coro, _main_loop)
    except RuntimeError as e:
        # run_coroutine_threadsafe raises before it ever schedules `coro`
        # in this case, so it's otherwise never awaited (a real
        # RuntimeWarning, confirmed visible in the test suite — see
        # run_on_main_loop's identical fix for the same underlying leak).
        coro.close()
        raise TimeoutError("coedit_room: main loop closed") from e
    return future.result(timeout=timeout)
