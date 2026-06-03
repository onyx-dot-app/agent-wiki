"""Per-MCP-session state.

Sessions are persisted in Postgres (``mcp_sessions`` table) so a wiki
server restart doesn't invalidate every client's ``Mcp-Session-Id``.
The live SSE stream and event queues still live in process memory —
see ``app/mcp_server/pubsub.py``. On reconnect the client's existing
ID is recognized, subscriptions rehydrate from
``mcp_path_subscriptions`` / ``mcp_job_subscriptions``, and the SSE
writer rebuilds its in-process queue.

For sessions with an active SSE stream on *this* process, the record
is also kept in the ``_local_sessions`` cache so ``get()`` and pubsub
fan-out don't pay a DB roundtrip per call. The cache is populated by
``adopt_local()`` (called by the SSE writer on stream open) and
evicted by ``drop()`` (SSE disconnect). DB row is retained on drop —
``terminate()`` is the explicit deletion path used by the cleanup
task.

``is_admin`` is cached on the row at creation and reused for the
lifetime of the session. Demotion takes effect at most one reconnect
later — the staleness only narrows access, so this matches the
``adopt_local`` refresh point.

Events fired *during* a server restart window are lost — there is no
durable event log. Clients should treat the SSE channel as best-effort
and rely on ``list_history`` for catch-up.
"""
from __future__ import annotations

import logging
import secrets
import threading
from datetime import datetime, timedelta, timezone

from pydantic import BaseModel, ConfigDict
from sqlalchemy import delete

from app.auth import User
from app.db import models as orm
from app.db.session import execute_dml, session as db_session

log = logging.getLogger(__name__)


_SESSION_TTL = timedelta(days=7)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")


class McpSession(BaseModel):
    """The state every MCP request reads or mutates.

    ``initialized`` flips to ``True`` on receipt of the
    ``notifications/initialized`` ack from the client; before that, only
    ``initialize`` is allowed. Mutate via ``mark_initialized()`` so the
    DB row stays in sync with the local-cache copy.

    ``is_admin`` is cached from the bearer-resolved User at session
    creation. See module docstring for the staleness trade-off.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    id: str
    user_id: str
    is_admin: bool = False
    initialized: bool = False


_local_sessions: dict[str, McpSession] = {}
_local_lock = threading.Lock()


def _new_id() -> str:
    return "mcps_" + secrets.token_urlsafe(16)


def _row_to_record(row: orm.McpSession) -> McpSession:
    return McpSession(
        id=row.id,
        user_id=row.user_id,
        is_admin=row.is_admin,
        initialized=row.initialized,
    )


def create(user: User) -> McpSession:
    """Mint a fresh session for ``user``, persist it, and add it to the
    local cache.

    Called from ``transport._handle_initialize``. Caller is responsible
    for flipping ``initialized`` via ``mark_initialized()`` once the
    client's ``notifications/initialized`` arrives.

    The session is added to ``_local_sessions`` at creation (not at
    SSE-open) so that an MCP client which posts ``initialize`` →
    ``tools/call`` without opening an SSE stream still receives
    locally-fanned-out notifications (e.g. ``list_changed``) into its
    in-memory queue. ``adopt_local`` on SSE-reconnect is what
    repopulates the cache for sessions that survived a restart.
    """
    sid = _new_id()
    now = _now()
    with db_session() as s:
        row = orm.McpSession(
            id=sid,
            user_id=user.id,
            is_admin=user.is_admin,
            initialized=False,
            created_at=_iso(now),
            last_used_at=_iso(now),
            expires_at=_iso(now + _SESSION_TTL),
        )
        s.add(row)
    sess = McpSession(id=sid, user_id=user.id, is_admin=user.is_admin, initialized=False)
    with _local_lock:
        _local_sessions[sid] = sess
    log.info("mcp session created id=%s user_id=%s", sid, user.id)
    return sess


def get(session_id: str | None) -> McpSession | None:
    """Return the session record, or ``None`` if unknown / expired.

    Reads the in-process cache first; falls back to Postgres so sessions
    survive restarts. Fallback reads do NOT auto-populate the cache —
    that's reserved for ``adopt_local()`` at SSE-stream open, so we
    don't accidentally treat ad-hoc cross-restart JSON-RPC calls as
    "locally active for pubsub fan-out".
    """
    if session_id is None:
        return None
    with _local_lock:
        cached = _local_sessions.get(session_id)
    if cached is not None:
        return cached
    with db_session() as s:
        row = s.get(orm.McpSession, session_id)
        if row is None:
            return None
        if row.expires_at < _iso(_now()):
            return None
        return _row_to_record(row)


def adopt_local(session_id: str) -> McpSession | None:
    """Promote ``session_id`` into the local cache and bump its expiry.

    Called by the SSE-stream open path after bearer-auth validation.
    Subsequent ``get()`` calls for this session resolve from cache, and
    ``all_session_ids()`` will include it for local fan-out. Returns
    ``None`` if the session is unknown or already expired.
    """
    now = _now()
    with db_session() as s:
        row = s.get(orm.McpSession, session_id)
        if row is None:
            return None
        if row.expires_at < _iso(now):
            return None
        row.last_used_at = _iso(now)
        row.expires_at = _iso(now + _SESSION_TTL)
        record = _row_to_record(row)
    with _local_lock:
        _local_sessions[session_id] = record
    return record


def mark_initialized(session_id: str | None) -> McpSession | None:
    """Flip ``initialized`` to True on receipt of
    ``notifications/initialized``. Persists and refreshes the local
    cache. Returns the refreshed record or ``None`` if the session is
    gone.
    """
    if session_id is None:
        return None
    with db_session() as s:
        row = s.get(orm.McpSession, session_id)
        if row is None:
            return None
        row.initialized = True
        record = _row_to_record(row)
    with _local_lock:
        if session_id in _local_sessions:
            _local_sessions[session_id] = record
    return record


def touch(session_id: str) -> None:
    """Bump ``last_used_at`` / ``expires_at`` so an active session
    doesn't expire under the cleanup task. Cheap to call repeatedly;
    keep cadence sensible (e.g. once per SSE heartbeat, not per
    notification).
    """
    now = _now()
    with db_session() as s:
        row = s.get(orm.McpSession, session_id)
        if row is None:
            return
        row.last_used_at = _iso(now)
        row.expires_at = _iso(now + _SESSION_TTL)


def all_session_ids() -> list[str]:
    """Snapshot of every session with an active SSE on *this* process.

    Used by ``pubsub.publish_list_changed`` for local fan-out — the
    NOTIFY bridge handles cross-process delivery, so other replicas
    walk their own local sets independently. Sessions whose SSE is
    closed are absent from this list, which is correct: there's no
    open stream to write to.
    """
    with _local_lock:
        return list(_local_sessions.keys())


def drop(session_id: str) -> None:
    """Release local SSE-bound resources for ``session_id``.

    Called from the SSE disconnect cleanup. Removes the in-process
    cache entry and tells pubsub to drop its in-memory queues, but does
    NOT delete the persistent ``mcp_sessions`` row — that's the whole
    point of persistent sessions: the client's ``Mcp-Session-Id`` stays
    valid across reconnects and server restarts.

    For permanent deletion (expiry cleanup, test teardown), call
    ``terminate()`` instead.
    """
    with _local_lock:
        existed = _local_sessions.pop(session_id, None) is not None
    if existed:
        log.info("mcp session SSE disconnected id=%s (DB row retained)", session_id)
    # Local import: pubsub depends on session for the ACL recheck, so
    # importing it at module load creates a cycle.
    from app.mcp_server import pubsub as mcp_pubsub

    mcp_pubsub.forget(session_id)


def terminate(session_id: str) -> None:
    """Permanently delete a session and cascade-delete its subscriptions.

    Called by the session-cleanup task on expiry and by tests during
    teardown.
    """
    with _local_lock:
        _local_sessions.pop(session_id, None)
    with db_session() as s:
        execute_dml(s, delete(orm.McpSession).where(orm.McpSession.id == session_id))
    from app.mcp_server import pubsub as mcp_pubsub

    mcp_pubsub.forget(session_id)


def reap_expired() -> int:
    """Delete every session whose ``expires_at`` is in the past. Cascade
    removes subscriptions. Returns the row count for logging."""
    now_iso = _iso(_now())
    with db_session() as s:
        count = execute_dml(s, delete(orm.McpSession).where(orm.McpSession.expires_at < now_iso))
    if count:
        log.info("mcp session cleanup: reaped %d expired sessions", count)
    return count


def reset_for_tests() -> None:
    """Drop every session — DB rows + local cache — and clear pubsub
    state. Tests call this between cases."""
    with _local_lock:
        _local_sessions.clear()
    with db_session() as s:
        execute_dml(s, delete(orm.McpSession))
    from app.mcp_server import pubsub as mcp_pubsub

    mcp_pubsub.reset_for_tests()
