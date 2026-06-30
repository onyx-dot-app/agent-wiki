"""Cross-process realtime message bus over Postgres LISTEN/NOTIFY.

A single dedicated connection ``LISTEN``s on one channel; publishers ``NOTIFY``
a JSON payload carrying a ``kind``. Each process stamps an origin tag and the
listener drops payloads carrying its own tag — local delivery already happened
in-process at publish time, so re-publishing the echo would duplicate it.

Consumers register one handler per ``kind`` (``register``); the listener routes
each incoming payload to the matching handler. This keeps the bus
transport-agnostic — it knows nothing about its consumers. Today those are the
MCP pubsub (``app/mcp_server/pubsub.py``) and the co-edit channel
(``app/wiki/coedit_channel.py``).

Raw ``LISTEN``/``NOTIFY`` is one of the few sanctioned raw-SQL sites (the ORM
can't express it), alongside ``app/db/fts.py`` and ``app/tasks/queue.py``.
"""

from __future__ import annotations

import json
import logging
import secrets
import threading
from collections.abc import Callable
from typing import Any

from app.db.session import session as db_session

log = logging.getLogger(__name__)

# Channel name is kept as ``wiki_commit`` so the bus interoperates with NOTIFYs
# emitted before the bus was extracted out of the MCP pubsub module.
CHANNEL = "wiki_commit"

# Per-process tag stamped into every payload. The listener drops payloads
# carrying our own tag — the originating process already delivered locally.
_PROCESS_ORIGIN = secrets.token_hex(8)

Handler = Callable[[dict[str, Any]], None]
_handlers: dict[str, Handler] = {}
_handlers_lock = threading.Lock()

_listener_thread: threading.Thread | None = None
_listener_stop = threading.Event()


def register(kind: str, handler: Handler) -> None:
    """Route incoming payloads whose ``kind`` equals ``kind`` to ``handler``.

    Handlers run on the listener thread and receive cross-process payloads only
    (a process never dispatches its own echo). Registering the same ``kind``
    twice replaces the prior handler.
    """
    with _handlers_lock:
        _handlers[kind] = handler


def emit(payload: dict[str, Any]) -> None:
    """``NOTIFY`` all replicas / the worker. Stamps the process origin so the
    sender skips its own echo. Best-effort: a transient connection error is
    logged and swallowed (local delivery already happened at the call site).

    Postgres' ``NOTIFY`` takes no bind parameters, so the JSON literal is
    inlined and single-quote-escaped (only the JSON could contain a quote).
    """
    try:
        from sqlalchemy import text

        literal = json.dumps({**payload, "origin": _PROCESS_ORIGIN}).replace("'", "''")
        with db_session() as s:
            s.execute(text(f"NOTIFY {CHANNEL}, '{literal}'"))
    except Exception:
        log.exception("realtime bus: NOTIFY %s failed (local delivery still occurred)", CHANNEL)


def _dispatch(raw: str) -> None:
    """Route one received NOTIFY payload to its registered handler."""
    try:
        payload = json.loads(raw)
    except (ValueError, TypeError):
        log.warning("realtime bus: malformed payload %r", raw)
        return
    if payload.get("origin") == _PROCESS_ORIGIN:
        # Our own echo — local delivery already happened at emit time.
        return
    kind = payload.get("kind")
    if not isinstance(kind, str):
        return
    with _handlers_lock:
        handler = _handlers.get(kind)
    if handler is None:
        return
    handler(payload)


def start_listener() -> None:
    """Start the LISTEN thread — call once from the web process at startup
    (``app/main.py``'s lifespan).

    The thread holds a single dedicated psycopg connection running
    ``LISTEN wiki_commit;`` and routes incoming notifications to registered
    handlers. Self-emitted notifications are skipped via the origin tag.
    """
    global _listener_thread
    if _listener_thread is not None and _listener_thread.is_alive():
        return
    _listener_stop.clear()
    _listener_thread = threading.Thread(
        target=_listener_loop,
        name="realtime-bus-listener",
        daemon=True,
    )
    _listener_thread.start()


def stop_listener() -> None:
    """Signal the listener to exit. Mostly for tests + clean shutdown."""
    _listener_stop.set()


def _listener_loop() -> None:
    """Block on the LISTEN connection and dispatch notifications.

    Uses raw psycopg (not SQLAlchemy) because LISTEN/NOTIFY needs a dedicated
    connection in autocommit mode held open across many notifications — outside
    the ORM session scope.
    """
    import psycopg

    from app.config import CONFIG

    while not _listener_stop.is_set():
        try:
            with psycopg.connect(CONFIG.database_url, autocommit=True) as conn:
                conn.execute(f"LISTEN {CHANNEL}")
                # ``notifies(timeout=...)`` STOPS once the timeout elapses (it
                # is not a per-item poll interval). Re-enter it on the same
                # connection — tearing the connection down between timeouts
                # would open a re-LISTEN gap where NOTIFYs are silently lost.
                while not _listener_stop.is_set():
                    for notify in conn.notifies(timeout=1.0):
                        if _listener_stop.is_set():
                            break
                        _dispatch(notify.payload)
        except Exception:
            log.exception("realtime bus: LISTEN loop crashed; restarting in 5s")
            _listener_stop.wait(5.0)
