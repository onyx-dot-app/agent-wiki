"""Persistent subscription registry + in-memory delivery + cross-process bus handlers.

Three concerns, separated:

1. **Persistent subscription state** (``mcp_path_subscriptions`` /
   ``mcp_job_subscriptions``). Durable across wiki-server restarts;
   the source of truth for "which session is subscribed to what." On
   SSE reconnect, ``register_async_consumer`` rehydrates the local
   in-memory index from these tables so the existing session resumes
   where it left off.

2. **In-memory delivery state** (``_queues`` / ``_async_queues`` /
   ``_subscribers_by_path`` / ``_subscribers_by_job``). Per-process,
   ephemeral, dies with the process. Holds the live SSE queues plus a
   reverse index used for *parking*: notifications for sessions that
   subscribed on this process but have no SSE open yet land on a
   bounded sync queue, drained at stream open (and peeked by the
   ``stale_paths`` tool for poll-based clients).

3. **Cross-process delivery via the shared realtime bus**
   (``app/realtime/bus.py``, Postgres LISTEN/NOTIFY). The worker
   process commits, a web process owns the SSE stream; ``publish_*``
   emits on the bus, and the handlers registered here (``_on_update``
   etc.) re-publish locally on every other replica. Self-originated
   payloads are skipped by the bus via an origin tag.

Fan-out on every publish (originating or relayed) targets
``db_subscribers(rel) ∩ (live ∪ parked)`` — the subscription tables
are the source of truth for *who is subscribed*; local state only
determines *who is reachable on this process*. This means a
``resources/subscribe`` handled by replica A is honored by the
replica that holds the session's SSE stream on the very next commit —
no sticky load balancing required for live delivery. Parking remains
replica-local best-effort: a subscribe-then-stream-open race only
replays the gap if both requests hit the same replica.

Events fired *during* a server restart window are lost — no durable
event log. Clients should rely on ``list_history`` for catch-up.

Per-subscriber ACL recheck before delivery — see ``_should_deliver``
— catches "doc went private mid-session". Both the notification and
the subscription (DB + cache) are dropped in that case; a subsequent
grant restores delivery on the agent's next ``read_doc`` +
auto-subscribe.
"""
from __future__ import annotations

import asyncio
import logging
import threading
from queue import Empty, Full, Queue
from typing import Any

from pydantic import BaseModel
from sqlalchemy import delete, select

from app.db import models as orm
from app.db.session import execute_dml, session as db_session
from app.models.wiki import ChangeKind
from app.realtime import bus

log = logging.getLogger(__name__)


class Notification(BaseModel):
    """A single SSE-deliverable notification — JSON-RPC frame shape."""

    method: str
    params: dict[str, Any]


# In-memory delivery state — per-process, rehydrated from Postgres on SSE
# reconnect. See module docstring for the consistency model.
#
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

# Cap on a parked (no live SSE) session's sync queue. Bounds memory when
# a session subscribes on this replica but its stream lives — or never
# opens — elsewhere. Oldest notifications are dropped first; they're
# re-read hints, so a client that drains a capped queue still converges
# by re-reading the docs it cares about.
_SYNC_QUEUE_MAX = 256


def _new_sync_queue() -> "Queue[Notification]":
    return Queue(maxsize=_SYNC_QUEUE_MAX)


# --------------------------------------------------------------------------- #
# Subscription bookkeeping                                                    #
# --------------------------------------------------------------------------- #


def subscribe_doc(session_id: str, rel: str) -> None:
    """Persist + cache a wiki-path subscription. Idempotent — duplicate
    subscribe is a no-op on both DB (UPSERT shape) and cache."""
    with db_session() as s:
        if s.get(orm.McpPathSubscription, (session_id, rel)) is None:
            s.add(orm.McpPathSubscription(session_id=session_id, rel_path=rel))
    with _lock:
        _subscriptions.setdefault(session_id, set()).add(rel)
        _subscribers_by_path.setdefault(rel, set()).add(session_id)
        _queues.setdefault(session_id, _new_sync_queue())


def unsubscribe_doc(session_id: str, rel: str) -> None:
    """Drop a wiki-path subscription from DB + cache."""
    with db_session() as s:
        execute_dml(
            s,
            delete(orm.McpPathSubscription).where(
                (orm.McpPathSubscription.session_id == session_id)
                & (orm.McpPathSubscription.rel_path == rel)
            ),
        )
    with _lock:
        if session_id in _subscriptions:
            _subscriptions[session_id].discard(rel)
        if rel in _subscribers_by_path:
            _subscribers_by_path[rel].discard(session_id)
            if not _subscribers_by_path[rel]:
                del _subscribers_by_path[rel]


def is_subscribed(session_id: str, rel: str) -> bool:
    """Return True iff ``session_id`` is subscribed to ``rel`` *on this
    process* (i.e. has been rehydrated into the local cache). Used by
    delivery decisions, not durability checks."""
    with _lock:
        return rel in _subscriptions.get(session_id, set())


def subscribe_job(session_id: str, job_id: str) -> None:
    """Persist + cache a ``job://<job_id>`` subscription. Auto-subscribed
    by the ``update_doc_nl`` wrapper at enqueue time so a chatty agent
    can poll ``stale_paths`` or read the SSE stream without a separate
    subscribe call."""
    with db_session() as s:
        if s.get(orm.McpJobSubscription, (session_id, job_id)) is None:
            s.add(orm.McpJobSubscription(session_id=session_id, job_id=job_id))
    with _lock:
        _job_subscriptions.setdefault(session_id, set()).add(job_id)
        _subscribers_by_job.setdefault(job_id, set()).add(session_id)
        _queues.setdefault(session_id, _new_sync_queue())


def unsubscribe_job(session_id: str, job_id: str) -> None:
    with db_session() as s:
        execute_dml(
            s,
            delete(orm.McpJobSubscription).where(
                (orm.McpJobSubscription.session_id == session_id)
                & (orm.McpJobSubscription.job_id == job_id)
            ),
        )
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
    """Return the session's currently-cached path subscriptions. Reads
    only the local cache — for a guaranteed view across replicas, query
    ``mcp_path_subscriptions`` directly."""
    with _lock:
        return set(_subscriptions.get(session_id, set()))


def queue_for(session_id: str) -> "Queue[Notification]":
    """Return the per-session sync queue. Creates it if missing — the
    SSE writer subscribes to a queue at stream start, before any
    notification might arrive."""
    with _lock:
        return _queues.setdefault(session_id, _new_sync_queue())


def _rehydrate_local(session_id: str) -> None:
    """Reload ``session_id``'s subscription cache from Postgres.

    Called on SSE-stream open. Idempotent — overwrites any stale local
    state for the session with the DB-authoritative set. After this
    returns, ``_subscribers_by_path`` / ``_subscribers_by_job`` contain
    the subscriber for every path/job they are currently subscribed to.
    """
    with db_session() as s:
        path_rows = s.execute(
            select(orm.McpPathSubscription.rel_path).where(
                orm.McpPathSubscription.session_id == session_id
            )
        ).all()
        job_rows = s.execute(
            select(orm.McpJobSubscription.job_id).where(
                orm.McpJobSubscription.session_id == session_id
            )
        ).all()
    paths = {row[0] for row in path_rows}
    jobs = {row[0] for row in job_rows}
    with _lock:
        # Clear any stale local entries first so removed subscriptions
        # don't linger in the reverse index.
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
        if paths:
            _subscriptions[session_id] = set(paths)
            for rel in paths:
                _subscribers_by_path.setdefault(rel, set()).add(session_id)
        if jobs:
            _job_subscriptions[session_id] = set(jobs)
            for job_id in jobs:
                _subscribers_by_job.setdefault(job_id, set()).add(session_id)
        _queues.setdefault(session_id, _new_sync_queue())


def forget(session_id: str) -> None:
    """Drop a session's *local* (in-process) state on SSE disconnect.

    Does NOT delete the persistent ``mcp_path_subscriptions`` /
    ``mcp_job_subscriptions`` rows — those carry across the disconnect
    and rehydrate on next reconnect. For full deletion (expiry, test
    teardown), the cascading FK on ``mcp_sessions`` handles it when
    ``mcp_session.terminate()`` runs.
    """
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
    """Clear in-process state. DB rows are cleared by
    ``mcp_session.reset_for_tests`` (CASCADE)."""
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

    Before registering the queue, the session's persistent
    subscriptions are reloaded from Postgres into the in-memory
    reverse index. This makes reconnect-after-restart transparent —
    the same ``Mcp-Session-Id`` resumes with the same set of watches.

    Drains any items already queued on the sync side at registration
    time — covers the small race window where a subscribe + publish
    happen before the SSE writer opens. After this returns, the async
    queue is the only path the writer drains.
    """
    _rehydrate_local(session_id)
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


def _deliver(session_id: str, notif: Notification) -> None:
    """Hand one notification to the session's active consumer.

    Exactly one destination: the asyncio queue when an SSE writer is
    registered (thread-safe via ``call_soon_threadsafe``), the sync
    queue otherwise. Writing to *both* — the pre-fix behavior — leaked
    memory (nothing drains the sync queue while the async consumer is
    live) and replayed the whole backlog as duplicates when
    ``register_async_consumer`` drained it on reconnect.
    """
    with _lock:
        q = _async_queues.get(session_id)
        loop = _async_loops.get(session_id)
        if q is None or loop is None:
            # No live SSE writer on this process — park on the sync
            # queue; ``register_async_consumer`` drains it at stream
            # open. The queue is bounded (drop-oldest) so a session
            # whose stream lives — or never opens — on another replica
            # can't grow it without limit.
            sync_q = _queues.setdefault(session_id, _new_sync_queue())
            while True:
                try:
                    sync_q.put_nowait(notif)
                    return
                except Full:
                    try:
                        sync_q.get_nowait()
                    except Empty:
                        pass
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
    bus.emit({"kind": "update", "rel": rel, "sha": sha, "change_kind": change_kind})


def publish_doc_delete(rel: str, sha: str) -> None:
    """Same shape as ``publish_doc_update`` but with ``changeKind="delete"``;
    subscribers can re-issue ``read_doc`` to confirm and unsubscribe."""
    _publish_local(rel, _build_update(rel, sha, ChangeKind.DELETE))
    bus.emit({"kind": "delete", "rel": rel, "sha": sha})


def publish_job_update(job_id: str, status: str) -> None:
    """Notify every session subscribed to ``job://<job_id>`` that its
    status changed. Called from the worker when a job transitions to
    ``running`` / ``succeeded`` / ``failed``.
    """
    _publish_local_job(job_id, _build_job_update(job_id, status))
    bus.emit({"kind": "job_update", "job_id": job_id, "status": status})


def publish_list_changed() -> None:
    """Tree shape changed (move / rename / new file). Every active
    MCP session — not just sessions that already have a subscription —
    re-fetches ``resources/list``.
    """
    notif = Notification(
        method="notifications/resources/list_changed",
        params={},
    )
    # Walk every locally-active session — sessions with an open SSE on
    # this process. Other replicas walk their own sets via the NOTIFY
    # relay below.
    from app.mcp_server import session as mcp_session  # noqa: PLC0415

    for session_id in mcp_session.all_session_ids():
        _deliver(session_id, notif)
    bus.emit({"kind": "list_changed"})


# --------------------------------------------------------------------------- #
# Internal — local fan-out + ACL recheck                                      #
# --------------------------------------------------------------------------- #


def _publish_local(rel: str, notif: Notification) -> None:
    """Deliver to ``db_subscribers(rel) ∩ (live ∪ parked)``.

    The subscription table decides *who is subscribed* — so a
    subscribe/unsubscribe handled by any replica takes effect here on
    the next publish, with no sticky-routing requirement. Local state
    decides *who is reachable on this process*: sessions with a live
    SSE writer (``_async_queues``) plus sessions parked in the local
    reverse index (subscribed here, stream not open yet).
    """
    with _lock:
        live = set(_async_queues.keys())
        parked = set(_subscribers_by_path.get(rel, set()))
    reachable = live | parked
    if not reachable:
        return  # nobody to deliver to on this process — skip the DB hit
    targets = _db_path_subscribers(rel) & reachable

    for session_id in targets:
        if not _should_deliver(session_id, rel):
            unsubscribe_doc(session_id, rel)
            log.info(
                "mcp pubsub: dropping subscriber session=%s rel=%s — lost ACL or session gone",
                session_id, rel,
            )
            continue
        _deliver(session_id, notif)


def _db_path_subscribers(rel: str) -> set[str]:
    """Session ids subscribed to ``rel`` per the durable table
    (``idx_mcp_path_subs_path`` makes this an index lookup)."""
    with db_session() as s:
        rows = s.execute(
            select(orm.McpPathSubscription.session_id).where(
                orm.McpPathSubscription.rel_path == rel
            )
        ).all()
    return {row[0] for row in rows}


def _db_job_subscribers(job_id: str) -> set[str]:
    """Session ids subscribed to ``job://<job_id>`` per the durable
    table (``idx_mcp_job_subs_job``)."""
    with db_session() as s:
        rows = s.execute(
            select(orm.McpJobSubscription.session_id).where(
                orm.McpJobSubscription.job_id == job_id
            )
        ).all()
    return {row[0] for row in rows}


def _should_deliver(session_id: str, rel: str) -> bool:
    """Per-subscriber ACL recheck. Returns False when the session is
    gone or its user can no longer read ``rel``."""
    # Local imports — these modules import this one transitively.
    from app.mcp_server import session as mcp_session  # noqa: PLC0415
    from app.wiki import acl  # noqa: PLC0415

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
    """Job-update fan-out — same ``db ∩ (live ∪ parked)`` shape as
    ``_publish_local`` but with no per-subscriber ACL recheck: a job is
    implicitly scoped to its owner; the ``resources/subscribe
    job://<id>`` handler refuses cross-user subscribes up front."""
    with _lock:
        live = set(_async_queues.keys())
        parked = set(_subscribers_by_job.get(job_id, set()))
    reachable = live | parked
    if not reachable:
        return
    for session_id in _db_job_subscribers(job_id) & reachable:
        _deliver(session_id, notif)


# --------------------------------------------------------------------------- #
# Cross-process delivery — handlers on the shared realtime bus                #
# --------------------------------------------------------------------------- #
#
# The bus (app/realtime/bus.py) owns the single LISTEN/NOTIFY connection and
# routes each incoming payload to the handler registered for its ``kind``.
# Handlers see cross-process payloads only — a process never dispatches its own
# echo — and re-publish them to this replica's local subscribers.


def _on_update(payload: dict[str, Any]) -> None:
    _publish_local(
        payload["rel"],
        _build_update(payload["rel"], payload["sha"], payload["change_kind"]),
    )


def _on_delete(payload: dict[str, Any]) -> None:
    _publish_local(
        payload["rel"],
        _build_update(payload["rel"], payload["sha"], ChangeKind.DELETE),
    )


def _on_list_changed(payload: dict[str, Any]) -> None:
    notif = Notification(method="notifications/resources/list_changed", params={})
    from app.mcp_server import session as mcp_session  # noqa: PLC0415

    for session_id in mcp_session.all_session_ids():
        _deliver(session_id, notif)


def _on_job_update(payload: dict[str, Any]) -> None:
    _publish_local_job(
        payload["job_id"],
        _build_job_update(payload["job_id"], payload["status"]),
    )


bus.register("update", _on_update)
bus.register("delete", _on_delete)
bus.register("list_changed", _on_list_changed)
bus.register("job_update", _on_job_update)


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
