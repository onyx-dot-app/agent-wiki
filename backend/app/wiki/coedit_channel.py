"""Live channel for co-editing — the SSE-down + POST-up transport.

Browser clients open one SSE connection per co-edit session (cookie-authed) and
the server pushes frames — presence now, edit ops later — to every connection in
the session. Cross-process delivery rides the existing ``wiki_commit``
LISTEN/NOTIFY bus in ``app/mcp_server/pubsub.py`` (a ``coedit`` payload kind), so
participants connected to different app servers still see each other.

This module is ephemeral by design: connection state (queues, loops) lives in
process memory and nothing here is persisted. Durable session/participant state
lives in ``app/wiki/coedit.py``. Frames are plain JSON-serializable dicts.

See ``Engineering Projects/Agent Wiki Project/design/Co-Editing.md``.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import uuid
from typing import Any

from app.mcp_server import pubsub

log = logging.getLogger(__name__)

Frame = dict[str, Any]

# Per-connection registry, keyed by an opaque connection id (one per open SSE
# stream). Parallel dicts rather than a class, mirroring ``pubsub``'s
# ``_async_queues`` / ``_async_loops`` style for the same runtime state.
_queues: dict[str, asyncio.Queue[Frame]] = {}
_loops: dict[str, asyncio.AbstractEventLoop] = {}
_session_of: dict[str, int] = {}  # conn_id -> coedit_session_id
_user_of: dict[str, str] = {}  # conn_id -> user_id
_conns_by_session: dict[int, set[str]] = {}  # coedit_session_id -> {conn_id}
_lock = threading.Lock()


def connect(coedit_session_id: int, user_id: str) -> tuple[str, asyncio.Queue[Frame]]:
    """Register a live connection for a session. Returns its id + queue.

    Must be called from inside the running event loop of the SSE handler — the
    loop is captured so cross-thread publishers (the LISTEN listener, sync
    request handlers) can hand frames in via ``call_soon_threadsafe``.
    """
    conn_id = uuid.uuid4().hex
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[Frame] = asyncio.Queue()
    with _lock:
        _queues[conn_id] = queue
        _loops[conn_id] = loop
        _session_of[conn_id] = coedit_session_id
        _user_of[conn_id] = user_id
        _conns_by_session.setdefault(coedit_session_id, set()).add(conn_id)
    return conn_id, queue


def disconnect(conn_id: str) -> None:
    """Tear down a connection's in-memory state."""
    with _lock:
        sid = _session_of.pop(conn_id, None)
        _queues.pop(conn_id, None)
        _loops.pop(conn_id, None)
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


async def drain(queue: asyncio.Queue[Frame], timeout: float) -> Frame | None:
    """Await the next frame up to ``timeout`` seconds; ``None`` on timeout so
    the SSE writer can emit a heartbeat."""
    try:
        return await asyncio.wait_for(queue.get(), timeout)
    except asyncio.TimeoutError:
        return None


def publish(coedit_session_id: int, frame: Frame) -> None:
    """Deliver ``frame`` to every connection in the session, on this process
    and (via NOTIFY) on every other."""
    _deliver_local(coedit_session_id, frame)
    pubsub.emit_external(
        {"kind": "coedit", "coedit_session_id": coedit_session_id, "frame": frame}
    )


def handle_remote(payload: dict[str, Any]) -> None:
    """Called by the pubsub LISTEN listener for a ``coedit`` NOTIFY from another
    process — local delivery only (no re-emit)."""
    _deliver_local(int(payload["coedit_session_id"]), payload["frame"])


def broadcast_presence(coedit_session_id: int) -> None:
    """Push the current participant roster to the session."""
    from app.wiki import coedit

    participants = coedit.list_participants(coedit_session_id)
    publish(
        coedit_session_id,
        {
            "type": "presence",
            "session_id": coedit_session_id,
            "participants": [p.model_dump() for p in participants],
        },
    )


def _deliver_local(coedit_session_id: int, frame: Frame) -> None:
    with _lock:
        conn_ids = list(_conns_by_session.get(coedit_session_id, ()))
        targets = [(cid, _queues.get(cid), _loops.get(cid)) for cid in conn_ids]
    for cid, queue, loop in targets:
        if queue is None or loop is None:
            continue
        try:
            loop.call_soon_threadsafe(queue.put_nowait, frame)
        except RuntimeError:
            # Loop already closed (connection tearing down) — drop the frame.
            log.debug("coedit: dropping frame for dead connection %s", cid)


def reset_for_tests() -> None:
    with _lock:
        _queues.clear()
        _loops.clear()
        _session_of.clear()
        _user_of.clear()
        _conns_by_session.clear()
