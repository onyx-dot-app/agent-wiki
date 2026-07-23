"""FastAPI dependencies + the request-scoped current-user middleware.

The session cookie is Starlette's ``SessionMiddleware`` native format
(signed via itsdangerous, configured once on the app in ``app.main``).
Reading ``request.session["user_id"]`` is all that's needed to
identify the caller; everything else flows through the
``current_user_ctx`` ContextVar set by :class:`CurrentUserMiddleware`.

Bearer-token auth for the MCP transport stays its own seam — it
parses the ``Authorization`` header and returns a :class:`User`
without going through ``request.session``.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from typing import Any

from fastapi import Depends, HTTPException, Request, WebSocket
from pydantic import BaseModel
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

from app.auth import User, current_user_ctx
from app.auth import mcp_tokens as tokens_repo
from app.auth import users as users_repo

log = logging.getLogger(__name__)

_BEARER_PREFIX = "Bearer "


def user_epoch(user_id: str) -> int:
    """Current ``session_epoch`` for stamping fresh sessions at login."""
    row = users_repo.get_by_id(user_id)
    return int((row or {}).get("session_epoch") or 0)


def _resolve_user(sess: dict[str, Any]) -> User | None:
    """Shared session-cookie -> ``User`` resolution, against a plain
    ``dict``-like session mapping — ``Request.session`` and
    ``WebSocket.session`` are both Starlette ``SessionMiddleware`` reads of
    the exact same signed cookie, so one resolver covers both transports."""
    user_id = sess.get("user_id")
    if not isinstance(user_id, str) or not user_id:
        return None
    row = users_repo.get_by_id(user_id)
    if row is None:
        return None
    if not row["is_active"]:
        # A deactivated user's existing session stops authenticating.
        return None
    # Sessions minted before a password change carry an older epoch (or none,
    # for pre-epoch cookies against a bumped account) and stop authenticating.
    if int(sess.get("session_epoch") or 0) != int(row["session_epoch"] or 0):
        return None
    return User(
        id=row["id"],
        email=row["email"],
        name=row["name"],
        is_admin=bool(row["is_admin"]),
    )


def current_user(request: Request) -> User | None:
    """Resolve the active user from the session cookie. Returns
    ``None`` when there is no cookie, the signature is invalid, the
    user row has been deleted, or the account is deactivated."""
    return _resolve_user(request.session)


def require_user(user: User | None = Depends(current_user)) -> User:
    if user is None:
        raise HTTPException(status_code=401, detail="unauthorized")
    return user


def current_user_ws(websocket: WebSocket) -> User | None:
    """WebSocket-flavored ``current_user`` — same cookie, same resolver."""
    return _resolve_user(websocket.session)


def require_user_ws(user: User | None = Depends(current_user_ws)) -> User:
    """Raises before the handshake completes on a missing/invalid session.
    Verified directly (not assumed): an ``HTTPException`` raised while
    FastAPI solves a websocket route's dependencies produces a clean HTTP
    denial response *before* the upgrade — the client sees a normal 401,
    the connection never opens — the same outcome ``require_user`` gives an
    HTTP caller, not a raw dropped socket."""
    if user is None:
        raise HTTPException(status_code=401, detail="unauthorized")
    return user


def require_admin(user: User = Depends(require_user)) -> User:
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="forbidden")
    return user


class BearerPrincipal(BaseModel):
    """A successfully-authenticated MCP bearer principal.

    ``agent_name`` is the token's user-supplied label — repurposed as
    the per-key agent identity that downstream code stamps onto
    ``AgentActivity`` rows and weaves into git commit authors.
    """

    user: User
    agent_name: str


def require_user_or_bearer(request: Request) -> User:
    """Accept either a session-cookie user OR a bearer token.

    Used by routes the launcher helper drives (heartbeat / cli-session /
    close on ``/api/agent-sessions/*``) — the helper has only the MCP
    bearer, the browser has only the session cookie. Both must work.
    """
    try:
        user = current_user(request)
    except HTTPException:
        # Only swallow auth failures (missing / invalid session). Any
        # other exception — DB outage, serialization error — must surface
        # as a 5xx, not get hidden behind a falls-through 401 from the
        # bearer path.
        user = None
    if user is not None:
        return user
    return require_bearer(request).user


def require_bearer(request: Request) -> BearerPrincipal:
    """Bearer-token auth for the inbound MCP transport. Distinct seam
    from session-cookie auth so a bad token returns a precise 401
    (``missing`` vs ``invalid``) and never falls through to the
    cookie-reader path."""
    header = request.headers.get("Authorization", "")
    if not header.startswith(_BEARER_PREFIX):
        raise HTTPException(status_code=401, detail="missing bearer token")
    raw = header[len(_BEARER_PREFIX) :].strip()
    resolved = tokens_repo.verify(raw)
    if resolved is None:
        log.info("mcp bearer rejected (token unrecognized)")
        raise HTTPException(status_code=401, detail="invalid bearer token")
    user, agent_name = resolved
    return BearerPrincipal(user=user, agent_name=agent_name)


class CurrentUserMiddleware(BaseHTTPMiddleware):
    """Resolve the session-cookie user once per request and bind it to
    the ``current_user_ctx`` ContextVar so downstream code —
    :func:`app.auth.current_user`, agent tools, :func:`require_can` —
    sees the principal without a request object.

    Bearer-token auth (MCP transport) is *not* handled here; that path
    binds the user inside :func:`require_bearer`. Middleware would
    have to parse the same header twice and we'd lose the per-token
    error distinction (missing vs invalid). Restricting middleware to
    session cookies keeps both surfaces independent.

    Has to be a middleware (not a dependency) because FastAPI runs
    sync dependencies in a fresh threadpool task per dep; a
    ``ContextVar.set`` from one sync dep won't propagate to the next
    or to the route handler. Middleware runs in the asyncio context
    which *is* copied to all child threads.
    """

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        user = current_user(request)
        token = current_user_ctx.set(user)
        try:
            return await call_next(request)
        finally:
            current_user_ctx.reset(token)
