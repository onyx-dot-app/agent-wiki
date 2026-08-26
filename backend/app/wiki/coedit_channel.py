"""Live channel for co-editing — transport-agnostic pub/sub, currently fed by
one ``WebSocket`` per session (``app/api/coedit.py``).

Browser clients open one connection per co-edit session (cookie-authed) and
the server relays two kinds of outbound traffic to every connection in the
session: raw Yjs sync/awareness protocol bytes (the live document itself —
``broadcast_yjs``), and small JSON control frames for everything that isn't
document content (presence roster, checkpoint acknowledgement —
``publish_control``). The WS route sends the former as binary frames, the
latter as text/JSON frames — that split is how a client tells them apart on
the wire.

Cross-process delivery rides the shared realtime bus (``app/realtime/bus.py``,
Postgres LISTEN/NOTIFY). Control frames go through unchanged (small, always
fits). Yjs payloads are base64'd into the same JSON envelope and, on the rare
oversized message (a large paste's CRDT update, or any payload that would
exceed Postgres's 8000-byte NOTIFY cap), split into sequential chunks tagged
with a shared group id and reassembled on the receiving end. Chunking rather
than a "tell the client to resync" fallback (what the old op-based
``broadcast_op`` did): a resync costs every connected editor their caret, and
splitting an oversized payload is both cheaper and lossless.

One thread-safe ``queue.Queue`` per connection, mirroring the MCP pubsub's
sync ``_queues`` / ``drain_blocking`` path — this module has no opinion on
how a connection drains its queue (``app/api/coedit.py``'s WS send loop
calls ``drain`` in a thread). Connection state is in-process and ephemeral —
nothing here is persisted; durable session/participant state lives in
``app/wiki/coedit.py``, and the live document itself lives in
``app/wiki/coedit_live.py``, rebuilt on demand.
"""

from __future__ import annotations

import base64
import logging
import queue
import threading
import time
import uuid
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, ConfigDict

from app.realtime import bus
from app.wiki import coedit

log = logging.getLogger(__name__)

ControlFrame = dict[str, Any]


class YjsBytes(BaseModel):
    """Wrapper marking a queued item as a raw Yjs protocol frame (sent as a
    WS binary frame) rather than a JSON control frame (sent as WS
    text/JSON) — the two are otherwise both just "something in the queue",
    and the send loop needs to know which wire method to use."""

    model_config = ConfigDict(frozen=True)

    payload: bytes
    # The ``ydoc_seq`` this update was assigned when logged, or ``None`` for
    # traffic that isn't logged (awareness, sync replies, a checkpoint's merge
    # delta — the snapshot carries that one durably). A dropped relay is
    # otherwise invisible, so the client needs to see the gap in the sequence
    # and fetch what it missed.
    seq: int | None = None


QueueItem = YjsBytes | ControlFrame


class Connection(BaseModel):
    """Handle for one live connection: its opaque id (for ``disconnect``), the
    queue items land in, and the callback that wakes its send loop.

    ``notify`` is invoked after every enqueue, from whichever thread published
    the item — a request handler, the bus listener, a task worker. The send
    loop supplies one that is safe to call from any thread (see
    ``app/api/coedit.py``); it exists so a connection can be woken without any
    thread blocking on its behalf.
    """

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    id: str
    queue: queue.Queue[QueueItem]
    notify: Callable[[], None]

    def post(self, item: QueueItem) -> None:
        """Queue ``item`` for this connection and wake its send loop.

        The only way to enqueue: a bare ``queue.put_nowait`` would land the
        item but leave the loop asleep until its next heartbeat.
        """
        self.queue.put_nowait(item)
        self.notify()


# Per-connection registry, keyed by an opaque connection id (one per open
# WebSocket). Parallel dicts rather than a class, mirroring ``pubsub``'s sync
# ``_queues`` style for the same runtime state.
_queues: dict[str, queue.Queue[QueueItem]] = {}
_notifiers: dict[str, Callable[[], None]] = {}  # conn_id -> wake its send loop
_session_of: dict[str, int] = {}  # conn_id -> coedit_session_id
_conns_by_session: dict[int, set[str]] = {}  # coedit_session_id -> {conn_id}
# Connections whose document proved to be a replaced lineage: binary Yjs
# frames are withheld from them (control frames still flow). See
# ``suppress_yjs``.
_suppressed_yjs: set[str] = set()
_lock = threading.Lock()


def connect(coedit_session_id: int, notify: Callable[[], None]) -> Connection:
    """Register a live connection for a session. Returns its handle.

    ``notify`` is called once per delivered item; see ``Connection``.
    """
    conn_id = uuid.uuid4().hex
    q: queue.Queue[QueueItem] = queue.Queue()
    with _lock:
        _queues[conn_id] = q
        _notifiers[conn_id] = notify
        _session_of[conn_id] = coedit_session_id
        _conns_by_session.setdefault(coedit_session_id, set()).add(conn_id)
    return Connection(id=conn_id, queue=q, notify=notify)


def disconnect(conn_id: str) -> None:
    """Tear down a connection's in-memory state."""
    with _lock:
        sid = _session_of.pop(conn_id, None)
        _queues.pop(conn_id, None)
        _notifiers.pop(conn_id, None)
        _suppressed_yjs.discard(conn_id)
        if sid is not None:
            conns = _conns_by_session.get(sid)
            if conns is not None:
                conns.discard(conn_id)
                if not conns:
                    _conns_by_session.pop(sid, None)



def drain(q: queue.Queue[QueueItem], timeout: float) -> QueueItem | None:
    """Pop the next item, waiting up to ``timeout`` seconds; ``None`` if none
    arrived. ``timeout=0`` polls without blocking."""
    try:
        return q.get(timeout=timeout)
    except queue.Empty:
        return None



def _deliver_local(coedit_session_id: int, frame: ControlFrame) -> None:
    # ``queue.Queue`` is thread-safe, so the put works from any thread (the
    # LISTEN listener, a request handler, a task worker). Waking the send loop
    # is the part that needs care, which is what ``notify`` encapsulates —
    # called outside the lock, since it hands off to another thread.
    with _lock:
        targets = [
            (_queues.get(cid), _notifiers.get(cid))
            for cid in _conns_by_session.get(coedit_session_id, ())
        ]
    delivered = 0
    for q, notify in targets:
        if q is None or notify is None:
            continue
        q.put_nowait(frame)
        notify()
        delivered += 1
    # A frame delivered to nobody *anywhere* is otherwise invisible: a
    # checkpoint ack that never lands leaves the client's save promise pending,
    # and without this there is no trace to distinguish "never published" from
    # "published, never delivered". A process holding no connection for the
    # session is the normal case rather than a fault — a task worker publishes
    # while the socket lives in the web process, reached via the bus — so this
    # records the counts and judges nothing.
    #
    # Only the checkpoint ack is worth INFO: it is the one whose loss is known
    # to strand a client, and it is rare (one per edit burst). Presence frames
    # go out on every join and leave, which would be noise at this level and
    # would bury the signal. They stay at DEBUG, where the whole trace is still
    # available when someone is looking for it.
    frame_type = frame.get("type", "?")
    log_at = log.info if frame_type == "checkpoint_result" else log.debug
    log_at(
        "coedit control frame %s session=%s delivered=%d of %d local conn(s)",
        frame_type,
        coedit_session_id,
        delivered,
        len(targets),
    )


def suppress_yjs(conn_id: str) -> None:
    """Stop delivering binary Yjs frames to one connection. Control frames
    still flow — ``resync_required`` must reach it. For a connection whose
    document proved to be a replaced lineage: feeding it more content only
    grows the client-side union it would try to sync back. Lasts for the
    connection's lifetime (cleared by ``disconnect``); recovery is a rebuilt
    document on a fresh connection.

    Binary frames already sitting in the connection's queue are drained too —
    a broadcast enqueued between registration and this call would otherwise
    still be delivered. A frame the send loop dequeued *before* this call can
    still go out; that residue is harmless (the doc is already condemned: its
    retired/old-lineage ids flag it foreign at its next sync regardless of
    what else it integrated)."""
    with _lock:
        _suppressed_yjs.add(conn_id)
        q = _queues.get(conn_id)
    if q is None:
        return
    kept: list[QueueItem] = []
    try:
        while True:
            item = q.get_nowait()
            if not isinstance(item, YjsBytes):
                kept.append(item)
    except queue.Empty:
        pass
    for item in kept:
        q.put_nowait(item)


def _deliver_local_bytes(
    coedit_session_id: int, payload: bytes, seq: int | None = None
) -> None:
    with _lock:
        targets = [
            (_queues.get(cid), _notifiers.get(cid))
            for cid in _conns_by_session.get(coedit_session_id, ())
            if cid not in _suppressed_yjs
        ]
    item = YjsBytes(payload=payload, seq=seq)
    for q, notify in targets:
        if q is None or notify is None:
            continue
        q.put_nowait(item)
        notify()


# --------------------------------------------------------------------------- #
# Control frames (presence, checkpoint ack) — small JSON, one bus "kind"      #
# --------------------------------------------------------------------------- #

_CONTROL_BUS_KIND = "coedit"


def publish_control(coedit_session_id: int, frame: ControlFrame) -> None:
    """Deliver a JSON control frame to every connection in the session, on
    this process and (via NOTIFY) on every other."""
    _deliver_local(coedit_session_id, frame)
    bus.emit({"kind": _CONTROL_BUS_KIND, "session_id": coedit_session_id, "frame": frame})


def _handle_remote_control(payload: dict[str, Any]) -> None:
    _deliver_local(int(payload["session_id"]), payload["frame"])


bus.register(_CONTROL_BUS_KIND, _handle_remote_control)


def broadcast_presence(coedit_session_id: int) -> None:
    """Push the current participant roster to the session. Participants
    (join/leave/viewer tracking) stay DB-backed and separate from Yjs
    Awareness — Awareness only reflects clients that have actually set
    local state, and this roster is also what the last-participant-leave
    checkpoint trigger keys off (see ``app/wiki/coedit.py``)."""
    participants = coedit.list_participants(coedit_session_id)
    publish_control(
        coedit_session_id,
        {
            "type": "presence",
            "session_id": coedit_session_id,
            "participants": [p.model_dump() for p in participants],
        },
    )


# --------------------------------------------------------------------------- #
# Yjs binary relay — chunked over the bus when a payload would exceed        #
# Postgres's NOTIFY size cap                                                  #
# --------------------------------------------------------------------------- #

_YJS_BUS_KIND = "coedit_yjs"
# Leaves room for the JSON envelope (session_id, i, n, group keys + quoting)
# around each base64 chunk, comfortably inside bus.MAX_PAYLOAD_BYTES.
_CHUNK_ENVELOPE_BUDGET = 200
_MAX_CHUNK_B64_LEN = bus.MAX_PAYLOAD_BYTES - _CHUNK_ENVELOPE_BUDGET

# Reassembly buffer for a chunked cross-process update, keyed by the sending
# process's group id. Bounded lifetime (``_PARTIAL_TTL_SECONDS``) so a lost
# chunk (a process restarting mid-send) can't leak a slot forever.
_partial_chunks: dict[str, list[str | None]] = {}
_partial_started_at: dict[str, float] = {}
_partial_lock = threading.Lock()
_PARTIAL_TTL_SECONDS = 30.0

# bus.emit() is a blocking Postgres NOTIFY round-trip, and broadcast_yjs fires
# on every content/awareness frame relayed by the WS route — several times a
# second per writer, more for a chunked large paste. A dedicated drain thread
# absorbs the blocking NOTIFY so the web process's own call is a cheap,
# non-blocking queue.put_nowait; it mirrors app/realtime/bus.py's own LISTEN
# thread and is started explicitly from app/main.py's lifespan, not at import
# time.
#
# Only the web process runs one. Workers broadcast too (a live-rebase fold, a
# diverged checkpoint's merge delta) and have no event loop to protect, so
# _queue_emit sends theirs inline — see its own comment.
_emit_queue: queue.Queue[dict[str, Any]] = queue.Queue()
_emit_thread: threading.Thread | None = None
_emit_stop = threading.Event()
# Whether this process ever started a drain thread — the difference between "a
# worker, emitting inline by design" and "the web process's thread died".
_emit_drain_started = False
# The dead-thread warning is once per process, not once per broadcast: the
# condition that trips it is permanent, and broadcasts run several times a
# second per writer, so warning each time would bury the log it belongs in.
_emit_drain_death_logged = False


def start_emit_drain() -> None:
    """Start the deferred cross-process emit thread — call once from the web
    process at startup (``app/main.py``'s lifespan), alongside
    ``bus.start_listener()``. A process that doesn't (a worker) emits inline
    instead; see ``_queue_emit``."""
    global _emit_thread, _emit_drain_started, _emit_drain_death_logged
    if _emit_thread is not None and _emit_thread.is_alive():
        return
    _emit_stop.clear()
    _emit_drain_started = True
    _emit_drain_death_logged = False
    _emit_thread = threading.Thread(
        target=_drain_emit_queue, name="coedit-emit-drain", daemon=True
    )
    _emit_thread.start()


def stop_emit_drain() -> None:
    """Signal the drain thread to exit. Mostly for tests + clean shutdown."""
    _emit_stop.set()


def _queue_emit(payload: dict[str, Any]) -> None:
    """Hand ``payload`` to the drain thread, or send it inline if there isn't one.

    The fallback is what makes a broadcast from a *worker* work: only the web
    process starts a drain thread, and queueing into a queue nothing drains
    would strand the frame silently — no error, just editors who never see an
    agent's fold. A worker has no event loop, so the blocking NOTIFY is fine
    there.

    ``_emit_stop`` is checked as well as liveness, because the two disagree for
    up to a second: the thread only notices the stop flag when its next
    ``get(timeout=1.0)`` returns, so it stays ``is_alive()`` after
    ``stop_emit_drain()`` while already being on its way out. Queueing into that
    window is the same silent drop, just at shutdown — and shutdown is exactly
    when a session's last frames are in flight.
    """
    if _emit_thread is not None and _emit_thread.is_alive() and not _emit_stop.is_set():
        _emit_queue.put_nowait(payload)
        return
    global _emit_drain_death_logged
    if _emit_drain_started and not _emit_stop.is_set() and not _emit_drain_death_logged:
        # A drain thread was started in this process and is not alive, and we
        # aren't shutting down — it died. Inline still delivers the frame, so
        # this is a warning rather than a failure, but it means broadcasts are
        # now blocking the caller.
        _emit_drain_death_logged = True
        log.warning(
            "coedit_channel: emit drain thread is gone; emitting inline (blocking)"
        )
    bus.emit(payload)  # best-effort; logs + swallows its own errors


def _drain_emit_queue() -> None:
    while not _emit_stop.is_set():
        try:
            payload = _emit_queue.get(timeout=1.0)
        except queue.Empty:
            continue
        bus.emit(payload)  # already best-effort — logs + swallows its own errors
    # Flush what's left on the way out. Whatever was queued before the stop
    # flag was set is still owed to the other processes, and dropping it here
    # would lose a session's final frames on every graceful shutdown.
    while True:
        try:
            bus.emit(_emit_queue.get_nowait())
        except queue.Empty:
            return


def broadcast_yjs(coedit_session_id: int, payload: bytes, seq: int | None = None) -> None:
    """Relay a raw Yjs sync/awareness protocol message to every connection in
    the session, this process and every other.

    No origin-exclusion: CRDT updates are idempotent, so even the sender
    re-receiving and re-applying its own message is a harmless no-op — the
    old op-based ``broadcast_op`` echoed to the sender too, for the same
    reason (relying on client-side ``client_id`` matching to skip re-
    applying, not on the server withholding the echo).

    The cross-process fan-out goes through ``_queue_emit`` — deferred to a
    drain thread in the web process, inline in a worker.
    """
    _deliver_local_bytes(coedit_session_id, payload, seq)
    b64 = base64.b64encode(payload).decode("ascii")
    if len(b64) <= _MAX_CHUNK_B64_LEN:
        _queue_emit(
            {"kind": _YJS_BUS_KIND, "session_id": coedit_session_id, "i": 0, "n": 1,
             "group": None, "chunk": b64, "seq": seq}
        )
        return
    group = uuid.uuid4().hex
    chunks = [b64[i : i + _MAX_CHUNK_B64_LEN] for i in range(0, len(b64), _MAX_CHUNK_B64_LEN)]
    for i, chunk in enumerate(chunks):
        _queue_emit(
            {
                "kind": _YJS_BUS_KIND,
                "session_id": coedit_session_id,
                "i": i,
                "n": len(chunks),
                "group": group,
                "chunk": chunk,
                "seq": seq,
            }
        )


def _cleanup_stale_partials_locked() -> None:
    now = time.monotonic()
    stale = [g for g, started in _partial_started_at.items() if now - started > _PARTIAL_TTL_SECONDS]
    for g in stale:
        _partial_chunks.pop(g, None)
        _partial_started_at.pop(g, None)
        log.warning("coedit_channel: dropped incomplete Yjs update chunk group %s (timed out)", g)


def _handle_remote_yjs(payload: dict[str, Any]) -> None:
    session_id = int(payload["session_id"])
    n = int(payload["n"])
    if n == 1:
        raw = base64.b64decode(payload["chunk"])
        _deliver_local_bytes(session_id, raw, payload.get("seq"))
        return
    group = payload["group"]
    full_b64: str | None = None
    with _partial_lock:
        _cleanup_stale_partials_locked()
        parts = _partial_chunks.setdefault(group, [None] * n)
        parts[int(payload["i"])] = payload["chunk"]
        _partial_started_at.setdefault(group, time.monotonic())
        if all(p is not None for p in parts):
            # The filtered generator (not a plain `"".join(parts)`) is what
            # lets basedpyright narrow each element to `str` here — `all()`
            # above doesn't propagate that narrowing back to `parts` itself.
            full_b64 = "".join(p for p in parts if p is not None)
            del _partial_chunks[group]
            del _partial_started_at[group]
    if full_b64 is not None:
        raw = base64.b64decode(full_b64)
        _deliver_local_bytes(session_id, raw, payload.get("seq"))


bus.register(_YJS_BUS_KIND, _handle_remote_yjs)


def reset_for_tests() -> None:
    with _lock:
        _queues.clear()
        _session_of.clear()
        _notifiers.clear()
        _conns_by_session.clear()
    with _partial_lock:
        _partial_chunks.clear()
        _partial_started_at.clear()
    # The emit queue and the drain-thread bookkeeping are process-global like the
    # rest: a frame left by an earlier test makes the next one's `get_nowait()`
    # return the wrong payload, and a leftover `_emit_drain_started` makes every
    # later test's inline emit look like a dead thread.
    global _emit_drain_started, _emit_drain_death_logged
    _emit_drain_started = False
    _emit_drain_death_logged = False
    while True:
        try:
            _emit_queue.get_nowait()
        except queue.Empty:
            break
