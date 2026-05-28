"""In-memory subscription registry + Postgres LISTEN/NOTIFY bridge.

Two responsibilities:

1. Track which MCP session is subscribed to which wiki path. This is
   per-process — sessions die with the process, subscriptions go with
   them. (Persistent subscriptions across reconnects is an explicit
   non-goal.)

2. Fan out a doc-change event to every subscribed session via a
   per-session in-memory queue. The SSE writer in
   ``app.mcp_server.transport`` drains the queue and ships JSON-RPC
   ``notifications/resources/updated`` frames over the open stream.

For cross-process delivery (the worker process commits, the web
process owns the SSE stream), the same event is also pushed onto
Postgres ``NOTIFY wiki_commit, '<json>'``. A long-lived listener
thread per web process — started via ``start_listener()`` from
``app/main.py:create_app`` — receives those notifications and
re-publishes them locally. Tests skip ``start_listener`` and just
exercise in-process delivery; ``publish_*`` always fans out locally
first, so the in-process test path doesn't depend on the bridge being
up.

Per-subscriber ACL recheck before delivery — see
``_should_deliver`` — catches "doc went private mid-session". Both
the notification and the subscription are dropped in that case; a
subsequent grant restores delivery on the agent's next ``read_doc`` +
auto-subscribe.
"""
from __future__ import annotations

import asyncio
import json
import logging
import threading
from queue import Empty, Queue
from typing import Any

from pydantic import BaseModel

from app.models.wiki import ChangeKind

log = logging.getLogger(__name__)

NOTIFY_CHANNEL = "wiki_commit"


class Notification(BaseModel):
    """A single SSE-deliverable notification — JSON-RPC frame shape."""

    method: str
    params: dict[str, Any]


# session_id → set of subscribed paths
_subscriptions: dict[str, set[str]] = {}
# rel_path → set of session_ids (reverse index for O(1) fan-out)
_subscribers_by_path: dict[str, set[str]] = {}
# session_id → set of subscribed job ids (job://<id> URIs)
_job_subscriptions: dict[str, set[str]] = {}
# job_id → set of session_ids
_subscribers_by_job: dict[str, set[str]] = {}
# session_id → pending-delivery queue (sync — Flask SSE writer drains this)
_queues: dict[str, "Queue[Notification]"] = {}
# session_id → (asyncio.Queue, owning event loop) — FastAPI SSE writer
# registers these at stream open via ``register_async_consumer``. A
# session has at most one async consumer at a time. Coexists with the
# sync ``_queues`` during the Phase 4-5 migration so both transports
# work; Phase 5 deletes the sync path.
_async_queues: dict[str, "asyncio.Queue[Notification]"] = {}
_async_loops: dict[str, asyncio.AbstractEventLoop] = {}

_lock = threading.Lock()


# --------------------------------------------------------------------------- #
# Subscription bookkeeping                                                    #
# --------------------------------------------------------------------------- #


def subscribe_doc(session_id: str, rel: str) -> None:
    with _lock:
        _subscriptions.setdefault(session_id, set()).add(rel)
        _subscribers_by_path.setdefault(rel, set()).add(session_id)
        _queues.setdefault(session_id, Queue())


def unsubscribe_doc(session_id: str, rel: str) -> None:
    with _lock:
        if session_id in _subscriptions:
            _subscriptions[session_id].discard(rel)
        if rel in _subscribers_by_path:
            _subscribers_by_path[rel].discard(session_id)
            if not _subscribers_by_path[rel]:
                del _subscribers_by_path[rel]


def is_subscribed(session_id: str, rel: str) -> bool:
    with _lock:
        return rel in _subscriptions.get(session_id, set())


def subscribe_job(session_id: str, job_id: str) -> None:
    """Register a session for ``job://<job_id>`` updates. The MCP-side
    ``update_doc_nl`` wrapper auto-subscribes the calling session at
    enqueue time so a chatty agent can poll ``stale_paths`` or read the
    SSE stream without a separate subscribe call."""
    with _lock:
        _job_subscriptions.setdefault(session_id, set()).add(job_id)
        _subscribers_by_job.setdefault(job_id, set()).add(session_id)
        _queues.setdefault(session_id, Queue())


def unsubscribe_job(session_id: str, job_id: str) -> None:
    with _lock:
        if session_id in _job_subscriptions:
            _job_subscriptions[session_id].discard(job_id)
        if job_id in _subscribers_by_job:
            _subscribers_by_job[job_id].discard(session_id)
            if not _subscribers_by_job[job_id]:
                del _subscribers_by_job[job_id]


def is_subscribed_job(session_id: str, job_id: str) -> bool:
    with _lock:
        return job_id in _job_subscriptions.get(session_id, set())


def subscriptions_for(session_id: str) -> set[str]:
    with _lock:
        return set(_subscriptions.get(session_id, set()))


def queue_for(session_id: str) -> "Queue[Notification]":
    """Return the per-session queue. Creates it if missing — the SSE
    writer subscribes to a queue at stream start, before any notification
    might arrive."""
    with _lock:
        return _queues.setdefault(session_id, Queue())


def forget(session_id: str) -> None:
    """Drop a session's subscriptions and queue — cleanup hook called
    from ``mcp_session.drop`` (which itself fires when the SSE generator
    exits)."""
    with _lock:
        for rel in _subscriptions.pop(session_id, set()):
            if rel in _subscribers_by_path:
                _subscribers_by_path[rel].discard(session_id)
                if not _subscribers_by_path[rel]:
                    del _subscribers_by_path[rel]
        for job_id in _job_subscriptions.pop(session_id, set()):
            if job_id in _subscribers_by_job:
                _subscribers_by_job[job_id].discard(session_id)
                if not _subscribers_by_job[job_id]:
                    del _subscribers_by_job[job_id]
        _queues.pop(session_id, None)
        _async_queues.pop(session_id, None)
        _async_loops.pop(session_id, None)


def reset_for_tests() -> None:
    with _lock:
        _subscriptions.clear()
        _subscribers_by_path.clear()
        _job_subscriptions.clear()
        _subscribers_by_job.clear()
        _queues.clear()
        _async_queues.clear()
        _async_loops.clear()


# --------------------------------------------------------------------------- #
# Async-consumer seam (FastAPI SSE writer)                                    #
# --------------------------------------------------------------------------- #


def register_async_consumer(session_id: str) -> "asyncio.Queue[Notification]":
    """Create-and-register an ``asyncio.Queue`` for the active session,
    bound to the currently-running event loop. Called by the FastAPI
    SSE writer at stream open. Subsequent publishes for ``session_id``
    enqueue via ``loop.call_soon_threadsafe`` so cross-thread pushes
    (from sync request handlers, task workers via PG NOTIFY, the
    LISTEN thread) hand off safely to the writer's loop.

    Drains any items already queued on the sync side at registration
    time — covers the small race window where a subscribe + publish
    happen before the SSE writer opens. After this returns, the async
    queue is the only path the writer drains."""
    loop = asyncio.get_running_loop()
    q: asyncio.Queue[Notification] = asyncio.Queue()
    with _lock:
        sync_q = _queues.get(session_id)
        pending: list[Notification] = []
        if sync_q is not None:
            try:
                while True:
                    pending.append(sync_q.get_nowait())
            except Empty:
                pass
        _async_queues[session_id] = q
        _async_loops[session_id] = loop
    for notif in pending:
        q.put_nowait(notif)
    return q


def _push_async(session_id: str, notif: Notification) -> None:
    """Thread-safe push to the per-session asyncio.Queue. No-op when no
    async consumer is registered (the only reader is the Flask SSE
    writer draining ``_queues`` via ``drain_blocking``)."""
    with _lock:
        q = _async_queues.get(session_id)
        loop = _async_loops.get(session_id)
    if q is None or loop is None:
        return
    try:
        loop.call_soon_threadsafe(q.put_nowait, notif)
    except RuntimeError:
        # Loop closed (consumer disconnected before cleanup ran). Drop
        # silently; the cleanup path will remove the stale registration
        # on the next ``forget``.
        log.debug("async push: loop closed for session %s", session_id)


async def drain_async(
    queue: "asyncio.Queue[Notification]", timeout: float,
) -> Notification | None:
    """Await the next notification with a timeout. Returns ``None`` on
    timeout so the SSE writer can emit a heartbeat / check liveness.
    Symmetric with the sync ``drain_blocking``."""
    try:
        return await asyncio.wait_for(queue.get(), timeout=timeout)
    except asyncio.TimeoutError:
        return None


# --------------------------------------------------------------------------- #
# Publish                                                                     #
# --------------------------------------------------------------------------- #


def publish_doc_update(rel: str, sha: str, change_kind: ChangeKind) -> None:
    """Fan out a write event to every subscribed session.

    Called from ``app.wiki.notify.after_doc_write`` (and from the
    LISTEN bridge for cross-process commits). Drops subscribers who
    have lost read access to ``rel`` since their last successful read.
    """
    _publish_local(rel, _build_update(rel, sha, change_kind))
    _emit_pg_notify({"kind": "update", "rel": rel, "sha": sha, "change_kind": change_kind})


def publish_doc_delete(rel: str, sha: str) -> None:
    """Same shape as ``publish_doc_update`` but with ``changeKind="delete"``;
    subscribers can re-issue ``read_doc`` to confirm and unsubscribe."""
    _publish_local(rel, _build_update(rel, sha, ChangeKind.DELETE))
    _emit_pg_notify({"kind": "delete", "rel": rel, "sha": sha})


def publish_job_update(job_id: str, status: str) -> None:
    """Notify every session subscribed to ``job://<job_id>`` that its
    status changed. Called from the worker when a job transitions to
    ``running`` / ``succeeded`` / ``failed``.
    """
    _publish_local_job(job_id, _build_job_update(job_id, status))
    _emit_pg_notify({"kind": "job_update", "job_id": job_id, "status": status})


def publish_list_changed() -> None:
    """Tree shape changed (move / rename / new file). Every active
    MCP session — not just sessions that already have a subscription —
    re-fetches ``resources/list``.
    """
    notif = Notification(
        method="notifications/resources/list_changed",
        params={},
    )
    # Walk every registered session so a brand-new session that hasn't
    # yet subscribed to anything still gets ``list_changed``. Tying
    # this to ``_queues`` (which only populates lazily on first
    # subscribe / first push) would silently miss those sessions.
    from app.mcp_server import session as mcp_session

    for session_id in mcp_session.all_session_ids():
        queue_for(session_id).put(notif)
        _push_async(session_id, notif)
    _emit_pg_notify({"kind": "list_changed"})


# --------------------------------------------------------------------------- #
# Internal — local fan-out + ACL recheck                                      #
# --------------------------------------------------------------------------- #


def _publish_local(rel: str, notif: Notification) -> None:
    with _lock:
        sessions = list(_subscribers_by_path.get(rel, set()))

    for session_id in sessions:
        if not _should_deliver(session_id, rel):
            unsubscribe_doc(session_id, rel)
            log.info(
                "mcp pubsub: dropping subscriber session=%s rel=%s — lost ACL or session gone",
                session_id, rel,
            )
            continue
        queue_for(session_id).put(notif)
        _push_async(session_id, notif)


def _should_deliver(session_id: str, rel: str) -> bool:
    """Per-subscriber ACL recheck. Returns False when the session is
    gone or its user can no longer read ``rel``."""
    # Local imports — these modules import this one transitively.
    from app.mcp_server import session as mcp_session
    from app.wiki import acl

    sess = mcp_session.get(session_id)
    if sess is None:
        return False
    return acl.can(sess.user_id, sess.is_admin, "read", rel)


def _build_update(rel: str, sha: str, change_kind: ChangeKind) -> Notification:
    return Notification(
        method="notifications/resources/updated",
        params={
            "uri": f"wiki:///{rel}",
            "changeKind": change_kind,
            "sha": sha,
        },
    )


def _build_job_update(job_id: str, status: str) -> Notification:
    return Notification(
        method="notifications/resources/updated",
        params={
            "uri": f"job://{job_id}",
            "status": status,
        },
    )


def _publish_local_job(job_id: str, notif: Notification) -> None:
    """Job-update fan-out — no per-subscriber ACL recheck because a
    job is implicitly scoped to its owner; the
    ``resources/subscribe job://<id>`` handler refuses cross-user
    subscribes up front."""
    with _lock:
        sessions = list(_subscribers_by_job.get(job_id, set()))
    for session_id in sessions:
        queue_for(session_id).put(notif)
        _push_async(session_id, notif)


# --------------------------------------------------------------------------- #
# Postgres LISTEN/NOTIFY bridge — opt-in (only the web process arms it)       #
# --------------------------------------------------------------------------- #


_listener_thread: threading.Thread | None = None
_listener_stop = threading.Event()


def _emit_pg_notify(payload: dict[str, Any]) -> None:
    """``NOTIFY wiki_commit, '<json>'`` so other replicas / the worker
    fan out to their local subscribers too. Best-effort; transient
    connection errors are swallowed and logged.

    Postgres' ``NOTIFY`` syntax doesn't accept parameter bindings — the
    payload literal has to be inlined into the SQL. We single-quote-
    escape it (doubling apostrophes) since the JSON itself is the only
    thing that could contain a quote.
    """
    try:
        from sqlalchemy import text

        from app.db.session import session as db_session

        literal = json.dumps(payload).replace("'", "''")
        with db_session() as s:
            s.execute(text(f"NOTIFY {NOTIFY_CHANNEL}, '{literal}'"))
    except Exception:
        # Don't let a NOTIFY hiccup take down the originating commit —
        # local delivery already happened, and the cross-process pipe
        # is not load-bearing for correctness.
        log.exception("mcp pubsub: NOTIFY %s failed (local delivery still occurred)", NOTIFY_CHANNEL)


def start_listener() -> None:
    """Start the LISTEN thread — call once from the web process at
    startup (``app/main.py:create_app``).

    The thread holds a single dedicated psycopg connection running
    ``LISTEN wiki_commit;`` and re-publishes incoming notifications
    locally. It's a no-op for self-emitted notifications (the local
    publish already happened in-process), but harmless because the
    queue dedup is implicit — two ``put`` calls just queue twice and
    the SSE writer drains both.
    """
    global _listener_thread
    if _listener_thread is not None and _listener_thread.is_alive():
        return

    _listener_stop.clear()
    _listener_thread = threading.Thread(
        target=_listener_loop,
        name="mcp-pubsub-listener",
        daemon=True,
    )
    _listener_thread.start()


def stop_listener() -> None:
    """Signal the listener to exit. Mostly for tests + clean shutdown."""
    _listener_stop.set()


def _listener_loop() -> None:
    """Block on the LISTEN connection, dispatch notifications back
    through ``_publish_local`` / ``publish_list_changed``.

    Uses raw psycopg (not SQLAlchemy) because LISTEN/NOTIFY needs a
    dedicated connection in autocommit mode held open across many
    notifications — outside the ORM session scope.
    """
    import psycopg

    from app.config import CONFIG

    while not _listener_stop.is_set():
        try:
            with psycopg.connect(CONFIG.database_url, autocommit=True) as conn:
                with conn.cursor() as cur:
                    cur.execute(f"LISTEN {NOTIFY_CHANNEL}")
                # generator yields notifies; .stop=True makes it abort on signal
                gen = conn.notifies(timeout=1.0)
                for notify in gen:
                    if _listener_stop.is_set():
                        break
                    _dispatch_notify_payload(notify.payload)
        except Exception:
            log.exception("mcp pubsub: LISTEN loop crashed; restarting in 5s")
            _listener_stop.wait(5.0)


def _dispatch_notify_payload(raw: str) -> None:
    """Translate a NOTIFY payload back into a local publish. Same shape
    we emit in ``_emit_pg_notify``."""
    try:
        payload = json.loads(raw)
    except (ValueError, TypeError):
        log.warning("mcp pubsub: malformed NOTIFY payload %r", raw)
        return
    kind = payload.get("kind")
    if kind == "update":
        _publish_local(
            payload["rel"],
            _build_update(payload["rel"], payload["sha"], payload["change_kind"]),
        )
    elif kind == "delete":
        _publish_local(
            payload["rel"],
            _build_update(payload["rel"], payload["sha"], ChangeKind.DELETE),
        )
    elif kind == "list_changed":
        # Same as ``publish_list_changed`` but skip the re-NOTIFY (we
        # got here from one).
        notif = Notification(
            method="notifications/resources/list_changed", params={}
        )
        from app.mcp_server import session as mcp_session

        for session_id in mcp_session.all_session_ids():
            queue_for(session_id).put(notif)
            _push_async(session_id, notif)
    elif kind == "job_update":
        _publish_local_job(
            payload["job_id"],
            _build_job_update(payload["job_id"], payload["status"]),
        )


# --------------------------------------------------------------------------- #
# Drain — used by the SSE writer                                              #
# --------------------------------------------------------------------------- #


def drain_blocking(session_id: str, timeout: float) -> Notification | None:
    """Block up to ``timeout`` seconds for the next notification on
    ``session_id``'s queue. Returns ``None`` on timeout so the SSE
    writer can emit a heartbeat / check liveness without keeping the
    request thread parked indefinitely.
    """
    try:
        return queue_for(session_id).get(timeout=timeout)
    except Empty:
        return None
