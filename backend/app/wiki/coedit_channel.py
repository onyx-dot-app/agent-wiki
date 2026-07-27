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
with a shared group id and reassembled on the receiving end — chosen over a
lossy "resync" fallback (what the old op-based ``broadcast_op`` did) because
there's no cheap "refetch the live doc" endpoint to resync *from* here: the
live document only exists as this process's in-memory ``pycrdt.Doc`` (see
``coedit_room.py``), not as a row a resync request could just re-read.

One thread-safe ``queue.Queue`` per connection, mirroring the MCP pubsub's
sync ``_queues`` / ``drain_blocking`` path — this module has no opinion on
how a connection drains its queue (``app/api/coedit.py``'s WS send loop
calls ``drain`` in a thread). Connection state is in-process and ephemeral —
nothing here is persisted; durable session/participant state lives in
``app/wiki/coedit.py``, and the live document itself lives in
``app/wiki/coedit_room.py``.
"""

from __future__ import annotations

import base64
import logging
import queue
import threading
import time
import uuid
from typing import Any

from pydantic import BaseModel, ConfigDict

from app.realtime import bus
from app.wiki import coedit

log = logging.getLogger(__name__)

ControlFrame = dict[str, Any]


class _CloseSignal:
    """Sentinel type for the one non-content value a connection's queue can
    carry. Distinct from ``None`` (which ``drain`` already uses to mean "the
    poll timed out, nothing arrived")."""


CLOSE_SIGNAL = _CloseSignal()


class YjsBytes(BaseModel):
    """Wrapper marking a queued item as a raw Yjs protocol frame (sent as a
    WS binary frame) rather than a JSON control frame (sent as WS
    text/JSON) — the two are otherwise both just "something in the queue",
    and the send loop needs to know which wire method to use."""

    model_config = ConfigDict(frozen=True)

    payload: bytes


QueueItem = YjsBytes | ControlFrame | _CloseSignal


class Connection(BaseModel):
    """Handle for one live connection: its opaque id (for ``disconnect``) and
    the queue its send loop drains."""

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    id: str
    queue: queue.Queue[QueueItem]


# Per-connection registry, keyed by an opaque connection id (one per open
# WebSocket). Parallel dicts rather than a class, mirroring ``pubsub``'s sync
# ``_queues`` style for the same runtime state.
_queues: dict[str, queue.Queue[QueueItem]] = {}
_session_of: dict[str, int] = {}  # conn_id -> coedit_session_id
_user_of: dict[str, str] = {}  # conn_id -> user_id
_conns_by_session: dict[int, set[str]] = {}  # coedit_session_id -> {conn_id}
_lock = threading.Lock()


def connect(coedit_session_id: int, user_id: str) -> Connection:
    """Register a live connection for a session. Returns its handle."""
    conn_id = uuid.uuid4().hex
    q: queue.Queue[QueueItem] = queue.Queue()
    with _lock:
        _queues[conn_id] = q
        _session_of[conn_id] = coedit_session_id
        _user_of[conn_id] = user_id
        _conns_by_session.setdefault(coedit_session_id, set()).add(conn_id)
    return Connection(id=conn_id, queue=q)


def disconnect(conn_id: str) -> None:
    """Tear down a connection's in-memory state."""
    with _lock:
        sid = _session_of.pop(conn_id, None)
        _queues.pop(conn_id, None)
        _user_of.pop(conn_id, None)
        if sid is not None:
            conns = _conns_by_session.get(sid)
            if conns is not None:
                conns.discard(conn_id)
                if not conns:
                    _conns_by_session.pop(sid, None)


def user_still_connected(coedit_session_id: int, user_id: str) -> bool:
    """True if ``user_id`` still has any open connection to the session.

    Lets the caller avoid firing ``leave`` when one of a user's several tabs
    closes while another stays open.
    """
    with _lock:
        return any(
            _user_of.get(cid) == user_id
            for cid in _conns_by_session.get(coedit_session_id, ())
        )


def drain(q: queue.Queue[QueueItem], timeout: float) -> QueueItem | None:
    """Block up to ``timeout`` seconds for the next item; ``None`` on timeout
    so the caller can emit a heartbeat. Can also return ``CLOSE_SIGNAL`` —
    see ``wake``."""
    try:
        return q.get(timeout=timeout)
    except queue.Empty:
        return None


def wake(conn_id: str) -> None:
    """Unblock a connection's own ``drain`` call immediately, instead of
    leaving it to run out its poll timeout.

    ``queue.Queue.get(timeout=...)`` has no cancellation hook — a task
    awaiting it via ``asyncio.to_thread`` can be cancelled at the asyncio
    level, but the underlying OS thread has no way to know that and keeps
    blocking regardless, for up to the full timeout, occupying a thread-pool
    slot the whole time. This reaches the blocked call the only way that's
    actually possible: pushing something into the same queue it's already
    waiting on, so ``get()`` returns immediately the normal way, on its own
    thread, same as a real item arriving. The caller (``app/api/coedit.py``'s
    teardown path) must call this whenever it's about to cancel a
    connection's send loop."""
    with _lock:
        q = _queues.get(conn_id)
    if q is not None:
        q.put_nowait(CLOSE_SIGNAL)


def _deliver_local(coedit_session_id: int, frame: ControlFrame) -> None:
    with _lock:
        targets = [
            _queues.get(cid) for cid in _conns_by_session.get(coedit_session_id, ())
        ]
    for q in targets:
        if q is not None:
            q.put_nowait(frame)


def _deliver_local_bytes(coedit_session_id: int, payload: bytes) -> None:
    with _lock:
        targets = [
            _queues.get(cid) for cid in _conns_by_session.get(coedit_session_id, ())
        ]
    item = YjsBytes(payload=payload)
    for q in targets:
        if q is not None:
            q.put_nowait(item)


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


def broadcast_yjs(coedit_session_id: int, payload: bytes) -> None:
    """Relay a raw Yjs sync/awareness protocol message to every connection in
    the session, this process and every other.

    No origin-exclusion: CRDT updates are idempotent, so even the sender
    re-receiving and re-applying its own message is a harmless no-op — the
    old op-based ``broadcast_op`` echoed to the sender too, for the same
    reason (relying on client-side ``client_id`` matching to skip re-
    applying, not on the server withholding the echo).
    """
    _deliver_local_bytes(coedit_session_id, payload)
    b64 = base64.b64encode(payload).decode("ascii")
    if len(b64) <= _MAX_CHUNK_B64_LEN:
        bus.emit(
            {"kind": _YJS_BUS_KIND, "session_id": coedit_session_id, "i": 0, "n": 1, "group": None, "chunk": b64}
        )
        return
    group = uuid.uuid4().hex
    chunks = [b64[i : i + _MAX_CHUNK_B64_LEN] for i in range(0, len(b64), _MAX_CHUNK_B64_LEN)]
    for i, chunk in enumerate(chunks):
        bus.emit(
            {
                "kind": _YJS_BUS_KIND,
                "session_id": coedit_session_id,
                "i": i,
                "n": len(chunks),
                "group": group,
                "chunk": chunk,
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
        _deliver_local_bytes(session_id, base64.b64decode(payload["chunk"]))
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
        _deliver_local_bytes(session_id, base64.b64decode(full_b64))


bus.register(_YJS_BUS_KIND, _handle_remote_yjs)


def reset_for_tests() -> None:
    with _lock:
        _queues.clear()
        _session_of.clear()
        _user_of.clear()
        _conns_by_session.clear()
    with _partial_lock:
        _partial_chunks.clear()
        _partial_started_at.clear()
