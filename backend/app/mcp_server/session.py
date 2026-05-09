"""Per-MCP-session state.

Sessions are in-memory and process-local: they live in
``_SESSIONS`` keyed by the ``Mcp-Session-Id`` header value. They die
when the process restarts — that's intentional ("persistent
subscriptions across reconnects" is an explicit non-goal in the design
doc). For multi-replica deploys the load balancer must pin sessions
to a replica via the ``Mcp-Session-Id`` header; v0 ships single-replica
so this is automatic.

Why a module-level dict and not a Postgres table: see the "Pub-sub"
section of ``local_data/wiki/mcp-server/mcp-server.md`` — earlier drafts
proposed a persistent ``mcp_subscriptions`` table; that idea was
deliberately dropped because subscriptions die with the session anyway.
"""
from __future__ import annotations

import logging
import secrets
import threading

from pydantic import BaseModel, ConfigDict, Field

from app.auth import User

log = logging.getLogger(__name__)


class McpSession(BaseModel):
    """The state every MCP request reads or mutates.

    ``initialized`` flips to ``True`` on receipt of the
    ``notifications/initialized`` ack from the client; before that, only
    ``initialize`` is allowed. ``seen_paths`` enforces the
    "must-have-read-before-edit" rule shared with the chat agent.

    ``is_admin`` is cached from the bearer-resolved User at session
    creation so the per-subscriber ACL recheck in
    ``app.mcp_server.pubsub`` doesn't have to hit the DB on every
    notification fan-out. If the user's admin status changes mid-
    session, the cache is stale until they reconnect — acceptable
    because the staleness only narrows access (an ex-admin still gets
    notifications for pages they have explicit grants on).
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    id: str
    user_id: str
    is_admin: bool = False
    initialized: bool = False
    seen_paths: set[str] = Field(default_factory=set)


_SESSIONS: dict[str, McpSession] = {}
_SESSIONS_LOCK = threading.Lock()


def _new_id() -> str:
    return "mcps_" + secrets.token_urlsafe(16)


def create(user: User) -> McpSession:
    """Mint a fresh session for ``user`` and register it.

    Called from ``transport._handle_initialize``. Caller is responsible
    for flipping ``initialized`` once the client's
    ``notifications/initialized`` arrives.
    """
    sess = McpSession(id=_new_id(), user_id=user.id, is_admin=user.is_admin)
    with _SESSIONS_LOCK:
        _SESSIONS[sess.id] = sess
    log.info("mcp session created id=%s user_id=%s", sess.id, user.id)
    return sess


def get(session_id: str | None) -> McpSession | None:
    if session_id is None:
        return None
    with _SESSIONS_LOCK:
        return _SESSIONS.get(session_id)


def all_session_ids() -> list[str]:
    """Snapshot of every active session id. Used by ``pubsub.publish_list_changed``
    to fan out a tree-shape change to every session, including ones
    that haven't subscribed to anything yet.
    """
    with _SESSIONS_LOCK:
        return list(_SESSIONS.keys())


def drop(session_id: str) -> None:
    """Remove a session — called from SSE-disconnect cleanup. Also drops
    the session's pubsub subscriptions and queue.
    """
    with _SESSIONS_LOCK:
        existed = _SESSIONS.pop(session_id, None) is not None
    if existed:
        log.info("mcp session dropped id=%s", session_id)
    # Local import: pubsub depends on session for the ACL recheck, so
    # importing it at module load creates a cycle.
    from app.mcp_server import pubsub as mcp_pubsub

    mcp_pubsub.forget(session_id)


def reset_for_tests() -> None:
    """Drop every session. Tests call this so the in-memory registry
    doesn't leak across cases. Also clears the pubsub registry so a
    follow-up test sees a clean publish/subscribe state.
    """
    with _SESSIONS_LOCK:
        _SESSIONS.clear()
    from app.mcp_server import pubsub as mcp_pubsub

    mcp_pubsub.reset_for_tests()
