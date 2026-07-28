"""FastAPI port of ``app/api/mcp_server.py``.

POST (JSON-RPC request/response) lands in Phase 3. GET (long-lived
SSE for server-initiated frames) is the heart of the migration's
payoff and lives here — one coroutine per idle MCP client instead
of one OS thread per client, which is the actual reason for the
ASGI move.

Adopting the upstream MCP Python SDK to replace the hand-rolled
JSON-RPC dispatcher is a follow-up PR; the existing dispatch is
preserved here so the cutover is small + reversible.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from typing import Any, cast

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import StreamingResponse

import re

from app.auth import User, set_current_user
from app.auth.deps import BearerPrincipal, require_bearer
from app.db import agent_sessions as agent_sessions_repo
from app.launchers.current_session import set_current_agent_session_id
from app.mcp_server import pubsub as mcp_pubsub
from app.mcp_server import session as mcp_session
from app.mcp_server.transport import UnknownSessionError, dispatch
from app.wiki import agent_activity

router = APIRouter()
log = logging.getLogger(__name__)

SESSION_HEADER = "Mcp-Session-Id"
AGENT_SESSION_HEADER = "X-Agentwiki-Session"
_AGENT_SESSION_RE = re.compile(r"^as_[a-zA-Z0-9-]{1,64}$")  # strict regex


def _resolve_agent_session(request: Request, user: User) -> tuple[str, str] | tuple[None, None]:
    """Validate + bind launcher session from X-Agentwiki-Session header.

    Returns ``(agent_session_id, tool_id)`` or ``(None, None)`` if the
    header is absent. Strict regex rejects header injection / overlong
    values. Cross-user 403 — bearer holder can't stamp a session that
    belongs to another user.
    """
    header = request.headers.get(AGENT_SESSION_HEADER)
    if not header:
        return None, None
    if not _AGENT_SESSION_RE.match(header):
        raise HTTPException(status_code=400, detail="malformed agent session id")
    row = agent_sessions_repo.get(header)
    if row is None:
        raise HTTPException(status_code=400, detail="unknown agent session id")
    if row["user_id"] != user.id:
        raise HTTPException(
            status_code=403,
            detail="agent session does not belong to this user",
        )
    agent_sessions_repo.touch_activity(header)
    return header, row["tool_id"]


# Same value the Flask transport uses — kept aligned so observers
# see the same idle behavior on both stacks during the transition.
_SSE_HEARTBEAT_SECONDS = 15.0


@router.post("")
async def transport_post(
    request: Request,
    principal: BearerPrincipal = Depends(require_bearer),
) -> Response:
    # Read the raw JSON body rather than binding a pydantic model. The
    # JSON-RPC dispatcher must own envelope validation so missing fields
    # come back as a JSON-RPC error envelope (code -32600) per spec,
    # not as FastAPI's 400 ``{"error": "<str>"}`` validation envelope.
    # Non-object bodies still return HTTP 400 since they aren't a valid
    # JSON-RPC message at all.
    try:
        body = await request.json()
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="invalid JSON body") from exc
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="body must be a JSON object")

    incoming = request.headers.get(SESSION_HEADER)
    user = principal.user
    # Resolve launcher agent_session_id from header BEFORE binding user
    # so cross-user 403 happens before any tool dispatch. When a launcher
    # session is present, its tool_id is the attribution label rather
    # than the bearer token's name (launcher tokens are generic per-user;
    # the session tells us which tool is actually driving this request).
    agent_sid, tool_id = _resolve_agent_session(request, user)
    agent_name = tool_id if tool_id is not None else principal.agent_name
    # Bind the bearer user, agent name, and agent_session_id to
    # ContextVars so downstream tool handlers stamp the right principal,
    # agent label, and session onto activity rows and git commit
    # authors. Bearer auth doesn't go through ``CurrentUserMiddleware``,
    # so we bind it here at the seam.
    agent_token = agent_activity.agent_name_var.set(agent_name)
    try:
        with set_current_user(user), set_current_agent_session_id(agent_sid):
            # dispatch() is a genuinely blocking call — tool handlers do
            # real DB/git work, and the four write tools reach a
            # synchronous LLM AI-merge — so calling it directly here
            # stalls this process's *entire* event loop for the duration,
            # not just this request: with the coedit WS route now also
            # running on this same loop (its own Doc math + every live
            # WebSocket connection in the process), one slow agent tool
            # call could freeze every live editing session on the pod
            # (confirmed in review). asyncio.to_thread copies the current
            # context (contextvars.copy_context()), so the
            # set_current_user/set_current_agent_session_id bindings above
            # are still visible to dispatch() on its worker thread.
            response, outgoing = await asyncio.to_thread(
                dispatch, cast("dict[str, Any]", body), incoming, user
            )
    except UnknownSessionError as exc:
        # Stale/unknown session id → 404 so the client starts a new session.
        return Response(
            content=json.dumps(exc.jsonrpc_error()),
            media_type="application/json",
            status_code=404,
        )
    finally:
        agent_activity.agent_name_var.reset(agent_token)

    headers: dict[str, str] = {}
    if outgoing:
        headers[SESSION_HEADER] = outgoing

    if response is None:
        # Notification — JSON-RPC convention: no body. 202 Accepted is
        # the MCP-recommended status for notification POSTs.
        return Response(status_code=202, headers=headers)

    return Response(
        content=json.dumps(response),
        media_type="application/json",
        status_code=200,
        headers=headers,
    )


@router.get("")
async def transport_sse(
    request: Request,
    principal: BearerPrincipal = Depends(require_bearer),
) -> Response:
    bearer_user = principal.user
    """Open the long-lived SSE stream for server-initiated frames.

    Idle clients cost one asyncio task each (not one OS thread). The
    publisher path bridges into the writer loop via
    ``mcp_pubsub.register_async_consumer`` / ``loop.call_soon_threadsafe``
    so cross-thread + cross-process notifications still reach this
    handler safely. On disconnect ``mcp_session.drop(session_id)``
    fires from the cleanup branch, which clears the session row, its
    sync queue, and its async queue.
    """
    sess_id = request.headers.get(SESSION_HEADER)
    if not sess_id:
        raise HTTPException(status_code=400, detail=f"missing {SESSION_HEADER} header")
    sess = mcp_session.get(sess_id)
    if sess is None:
        # Unknown/expired id (header present) → 404 so the client re-inits.
        raise HTTPException(status_code=404, detail="missing or invalid Mcp-Session-Id")
    if not sess.initialized:
        raise HTTPException(status_code=400, detail="session not initialized")
    if sess.user_id != bearer_user.id:
        # Bearer resolves to user A but the supplied session id was
        # minted for user B — refuse rather than risk hijack.
        raise HTTPException(
            status_code=403,
            detail="session does not belong to this bearer",
        )

    # Promote the session into the local cache and bump its expiry —
    # subsequent publishes for ``sess_id`` will see it in
    # ``mcp_session.all_session_ids()`` for tree-shape fan-out, and the
    # per-request ``mcp_session.get`` calls resolve from cache instead
    # of round-tripping to Postgres.
    if mcp_session.adopt_local(sess_id) is None:
        # Race: session expired between the ``get`` above and now. Treat
        # as a fresh 404 so the client re-initializes.
        raise HTTPException(status_code=404, detail="missing or invalid Mcp-Session-Id")

    # Bind an asyncio.Queue + this loop into pubsub so subsequent
    # publishes for ``sess_id`` enqueue here via call_soon_threadsafe.
    # ``register_async_consumer`` also rehydrates the in-memory
    # subscription index from Postgres for this session.
    queue = mcp_pubsub.register_async_consumer(sess_id)

    async def stream() -> AsyncIterator[bytes]:
        try:
            while True:
                if await request.is_disconnected():
                    break
                notif = await mcp_pubsub.drain_async(queue, _SSE_HEARTBEAT_SECONDS)
                if notif is None:
                    # Heartbeat comment — keeps proxies from idling the
                    # connection without showing up in the JSON-RPC
                    # event stream.
                    yield b": keepalive\n\n"
                    continue
                frame: dict[str, Any] = {
                    "jsonrpc": "2.0",
                    "method": notif.method,
                    "params": notif.params,
                }
                yield f"data: {json.dumps(frame)}\n\n".encode("utf-8")
        except asyncio.CancelledError:
            # FastAPI cancels the generator when the client disconnects.
            raise
        finally:
            mcp_session.drop(sess_id)
            log.info("mcp sse stream closed session=%s", sess_id)

    headers = {
        # ``no-transform`` makes the Next.js dev-server compression
        # middleware skip gzip. nginx in prod honors
        # ``X-Accel-Buffering: no`` for the same end goal.
        "Cache-Control": "no-cache, no-transform",
        "X-Accel-Buffering": "no",
    }
    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers=headers,
    )
