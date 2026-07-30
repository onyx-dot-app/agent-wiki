"""Live channel for co-editing — transport-agnostic pub/sub, currently fed by
one ``WebSocket`` per session (``app/api/coedit.py``).

Browser clients open one connection per co-edit session (cookie-authed) and
the server pushes session frames (e.g. presence) to every connection in the
session. Cross-process delivery rides the shared realtime bus
(``app/realtime/bus.py``, Postgres LISTEN/NOTIFY) under a ``coedit`` payload
kind, so participants connected to different app servers still see each other.

One thread-safe ``queue.Queue`` per connection, mirroring the MCP pubsub's
sync ``_queues`` / ``drain_blocking`` path — this module has no opinion on
how a connection drains its queue (``app/api/coedit.py``'s WS send loop
calls ``drain`` in a thread). Frames are plain JSON-serializable dicts.
Delivery queues are in-process and ephemeral. Durable sessions and the shared
participant heartbeat live in ``app/wiki/coedit.py``.

See ``Engineering Projects/Agent Wiki Project/design/Co-Editing.md``.
"""

from __future__ import annotations

import logging
import queue
import threading
import uuid
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, ConfigDict

from app.realtime import bus
from app.wiki import coedit
from app.wiki.coedit import Change

log = logging.getLogger(__name__)

Frame = dict[str, Any]


class Connection(BaseModel):
    """Handle for one live connection: its opaque id (for ``disconnect``), the
    queue frames land in, and the callback that tells its send loop to drain.

    ``notify`` is invoked after every enqueue, from whichever thread published
    the frame — a request handler, the bus listener, a task worker. The send
    loop supplies one that is safe to call from any thread (see
    ``app/api/coedit.py``); it exists so a connection can be woken without any
    thread blocking on its behalf.
    """

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    id: str
    queue: queue.Queue[Frame]
    notify: Callable[[], None]

    def post(self, frame: Frame) -> None:
        """Queue ``frame`` for this connection and wake its send loop.

        The only way to enqueue: a bare ``queue.put_nowait`` would land the
        frame but leave the loop asleep until its next heartbeat.
        """
        self.queue.put_nowait(frame)
        self.notify()


# Per-connection registry, keyed by an opaque connection id (one per open
# WebSocket). Parallel dicts rather than a class, mirroring ``pubsub``'s sync
# ``_queues`` style for the same runtime state.
_queues: dict[str, queue.Queue[Frame]] = {}
_notifiers: dict[str, Callable[[], None]] = {}  # conn_id -> wake its send loop
_session_of: dict[str, int] = {}  # conn_id -> coedit_session_id
_conns_by_session: dict[int, set[str]] = {}  # coedit_session_id -> {conn_id}
_lock = threading.Lock()


def connect(coedit_session_id: int, notify: Callable[[], None]) -> Connection:
    """Register a local delivery queue for a WebSocket.

    ``notify`` is called once per delivered frame; see ``Connection``.
    """
    conn_id = uuid.uuid4().hex
    q: queue.Queue[Frame] = queue.Queue()
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
        if sid is not None:
            conns = _conns_by_session.get(sid)
            if conns is not None:
                conns.discard(conn_id)
                if not conns:
                    _conns_by_session.pop(sid, None)

def drain(q: queue.Queue[Frame], timeout: float) -> Frame | None:
    """Pop the next frame, waiting up to ``timeout`` seconds; ``None`` if none
    arrived. ``timeout=0`` polls without blocking."""
    try:
        return q.get(timeout=timeout)
    except queue.Empty:
        return None


def _bus_payload(coedit_session_id: int, frame: Frame) -> dict[str, Any]:
    return {"kind": "coedit", "coedit_session_id": coedit_session_id, "frame": frame}


def publish(coedit_session_id: int, frame: Frame) -> None:
    """Deliver ``frame`` to every connection in the session, on this process and
    (via NOTIFY) on every other."""
    _deliver_local(coedit_session_id, frame)
    bus.emit(_bus_payload(coedit_session_id, frame))


def handle_remote(payload: dict[str, Any]) -> None:
    """Bus handler for a ``coedit`` NOTIFY from another process — local delivery
    only (no re-emit)."""
    _deliver_local(int(payload["coedit_session_id"]), payload["frame"])


bus.register("coedit", handle_remote)


def broadcast_presence(coedit_session_id: int) -> None:
    """Push the current participant roster to the session."""
    participants = coedit.list_participants(coedit_session_id)
    publish(
        coedit_session_id,
        {
            "type": "presence",
            "session_id": coedit_session_id,
            "participants": [p.model_dump() for p in participants],
        },
    )


def broadcast_cursor(
    coedit_session_id: int,
    *,
    user_id: str,
    user_display: str,
    anchor: int | None,
    head: int | None,
    typing: bool,
    seq: int | None,
) -> None:
    """Broadcast a participant's live cursor/selection — an ephemeral frame,
    never persisted. A collapsed selection (anchor == head) is a caret; a range
    is a selection highlight; null anchor/head means the sender cleared their
    caret (peers drop it). ``seq`` is the sender's caret epoch — peers drop
    frames older than the latest epoch they've seen, so concurrently delivered
    place/clear frames can't apply out of order. Peers shift held offsets
    client-side as ops land."""
    publish(
        coedit_session_id,
        {
            "type": "cursor",
            "session_id": coedit_session_id,
            "user_id": user_id,
            "user_display": user_display,
            "anchor": anchor,
            "head": head,
            "typing": typing,
            "seq": seq,
        },
    )


def broadcast_resync(coedit_session_id: int, version: int) -> None:
    """Tell the session its buffer was replaced out from under the op stream —
    peers re-fetch via ``GET /coedit/session``.

    Used when an inbound agent/ingest commit is reconciled into the buffer
    (live-rebase) or a checkpoint syncs its merged result back. The change is a
    git commit, not a co-edit op, so it doesn't carry a per-keystroke delta —
    participants reload the buffer at the new ``version``."""
    publish(coedit_session_id, {"type": "resync", "session_id": coedit_session_id, "version": version})


def broadcast_op(
    coedit_session_id: int,
    version: int,
    changes: list[Change],
    author_user_id: str,
    client_id: str | None = None,
    caret_seq: int | None = None,
) -> None:
    """Broadcast an applied edit op to the session's other connections.

    Normally the op frame carries the changes so peers apply them directly. If
    the serialized payload would exceed the bus's NOTIFY cap (a large paste),
    fall back to a ``resync`` signal — peers re-fetch the buffer via
    ``GET /coedit/session`` instead of us dropping the update.

    ``client_id`` (the originating connection) rides along so a collaborative
    client can tell its own echoed op from a peer's. ``caret_seq`` is the
    author's caret epoch when placed (an edit asserts caret placement); None
    means the op carries no caret assertion.
    """
    frame: Frame = {
        "type": "op",
        "session_id": coedit_session_id,
        "version": version,
        "changes": [c.model_dump(by_alias=True) for c in changes],
        "author": author_user_id,
        "client_id": client_id,
        "caret_seq": caret_seq,
    }
    if not bus.payload_fits(_bus_payload(coedit_session_id, frame)):
        frame = {"type": "resync", "session_id": coedit_session_id, "version": version}
    publish(coedit_session_id, frame)


def _deliver_local(coedit_session_id: int, frame: Frame) -> None:
    # ``queue.Queue`` is thread-safe, so the put works from any thread (the
    # LISTEN listener, a request handler, a task worker). Waking the send loop
    # is the part that needs care, which is what ``notify`` encapsulates —
    # called outside the lock, since it hands off to another thread.
    with _lock:
        targets = [
            (_queues.get(cid), _notifiers.get(cid))
            for cid in _conns_by_session.get(coedit_session_id, ())
        ]
    for q, notify in targets:
        if q is None or notify is None:
            continue
        q.put_nowait(frame)
        notify()


def reset_for_tests() -> None:
    with _lock:
        _queues.clear()
        _notifiers.clear()
        _session_of.clear()
        _conns_by_session.clear()
