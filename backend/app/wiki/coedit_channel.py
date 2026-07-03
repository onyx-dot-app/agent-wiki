"""Live channel for co-editing — the SSE-down + POST-up transport.

Browser clients open one SSE connection per co-edit session (cookie-authed) and
the server pushes session frames (e.g. presence) to every connection in the
session. Cross-process delivery rides the shared realtime bus
(``app/realtime/bus.py``, Postgres LISTEN/NOTIFY) under a ``coedit`` payload
kind, so participants connected to different app servers still see each other.

One thread per connection with a thread-safe ``queue.Queue``, mirroring the MCP
pubsub's sync ``_queues`` / ``drain_blocking`` path. Connection state is
in-process and ephemeral — nothing here is persisted; durable
session/participant state lives in ``app/wiki/coedit.py``. Frames are plain
JSON-serializable dicts.

See ``Engineering Projects/Agent Wiki Project/design/Co-Editing.md``.
"""

from __future__ import annotations

import logging
import queue
import threading
import uuid
from typing import Any

from pydantic import BaseModel, ConfigDict

from app.realtime import bus
from app.wiki import coedit
from app.wiki.coedit import Change

log = logging.getLogger(__name__)

Frame = dict[str, Any]


class Connection(BaseModel):
    """Handle for one live SSE connection: its opaque id (for ``disconnect``)
    and the queue the stream generator drains."""

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    id: str
    queue: queue.Queue[Frame]

# Per-connection registry, keyed by an opaque connection id (one per open SSE
# stream). Parallel dicts rather than a class, mirroring ``pubsub``'s sync
# ``_queues`` style for the same runtime state.
_queues: dict[str, queue.Queue[Frame]] = {}
_session_of: dict[str, int] = {}  # conn_id -> coedit_session_id
_user_of: dict[str, str] = {}  # conn_id -> user_id
_conns_by_session: dict[int, set[str]] = {}  # coedit_session_id -> {conn_id}
_lock = threading.Lock()


def connect(coedit_session_id: int, user_id: str) -> Connection:
    """Register a live connection for a session. Returns its handle."""
    conn_id = uuid.uuid4().hex
    q: queue.Queue[Frame] = queue.Queue()
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

    Lets the SSE handler avoid firing ``leave`` when one of a user's several
    tabs closes while another stays open.
    """
    with _lock:
        return any(
            _user_of.get(cid) == user_id
            for cid in _conns_by_session.get(coedit_session_id, ())
        )


def drain(q: queue.Queue[Frame], timeout: float) -> Frame | None:
    """Block up to ``timeout`` seconds for the next frame; ``None`` on timeout so
    the SSE writer can emit a heartbeat."""
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
    anchor: int,
    head: int,
    typing: bool,
) -> None:
    """Broadcast a participant's live cursor/selection — an ephemeral frame,
    never persisted. A collapsed selection (anchor == head) is a caret; a range
    is a selection highlight. Peers shift these offsets client-side as ops land."""
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
) -> None:
    """Broadcast an applied edit op to the session's other connections.

    Normally the op frame carries the changes so peers apply them directly. If
    the serialized payload would exceed the bus's NOTIFY cap (a large paste),
    fall back to a ``resync`` signal — peers re-fetch the buffer via
    ``GET /coedit/session`` instead of us dropping the update.

    ``client_id`` (the originating connection) rides along so a collaborative
    client can tell its own echoed op from a peer's.
    """
    frame: Frame = {
        "type": "op",
        "session_id": coedit_session_id,
        "version": version,
        "changes": [c.model_dump(by_alias=True) for c in changes],
        "author": author_user_id,
        "client_id": client_id,
    }
    if not bus.payload_fits(_bus_payload(coedit_session_id, frame)):
        frame = {"type": "resync", "session_id": coedit_session_id, "version": version}
    publish(coedit_session_id, frame)


def _deliver_local(coedit_session_id: int, frame: Frame) -> None:
    # ``queue.Queue`` is thread-safe, so a plain ``put_nowait`` works from any
    # thread (the LISTEN listener, a request handler) — no event loop, no
    # cross-thread scheduling dance.
    with _lock:
        targets = [
            _queues.get(cid) for cid in _conns_by_session.get(coedit_session_id, ())
        ]
    for q in targets:
        if q is not None:
            q.put_nowait(frame)


def reset_for_tests() -> None:
    with _lock:
        _queues.clear()
        _session_of.clear()
        _user_of.clear()
        _conns_by_session.clear()
