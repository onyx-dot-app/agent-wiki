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
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import StreamingResponse

from app.auth import User, set_current_user
from app.auth.deps import require_bearer
from app.mcp_server import pubsub as mcp_pubsub
from app.mcp_server import session as mcp_session
from app.mcp_server.transport import dispatch
from app.models.mcp import JsonRpcRequest

router = APIRouter()
log = logging.getLogger(__name__)

SESSION_HEADER = "Mcp-Session-Id"

# Same value the Flask transport uses — kept aligned so observers
# see the same idle behavior on both stacks during the transition.
_SSE_HEARTBEAT_SECONDS = 15.0


@router.post("")
async def transport_post(
    rpc: JsonRpcRequest,
    request: Request,
    user: User = Depends(require_bearer),
) -> Response:
    # The dispatcher reads "id absent" to detect JSON-RPC notifications,
    # so we feed it only the fields the client actually sent. ``extra``
    # is allowed on the envelope to keep forward-compat with new
    # protocol fields, so we round-trip those too.
    body: dict[str, Any] = rpc.model_dump(exclude_unset=True)

    incoming = request.headers.get(SESSION_HEADER)
    # Bind the bearer user to the ContextVar so downstream tool handlers
    # (which call ``current_user()`` to read the principal) see the
    # right user. Bearer auth doesn't go through the cookie-reading
    # ``CurrentUserMiddleware``, so we bind it here at the seam.
    with set_current_user(user):
        response, outgoing = dispatch(body, incoming, user)

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
    request: Request, bearer_user: User = Depends(require_bearer),
) -> Response:
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
    if sess is None or not sess.initialized:
        raise HTTPException(status_code=400, detail="session not initialized")
    if sess.user_id != bearer_user.id:
        # Bearer resolves to user A but the supplied session id was
        # minted for user B — refuse rather than risk hijack.
        raise HTTPException(
            status_code=403, detail="session does not belong to this bearer",
        )

    # Bind an asyncio.Queue + this loop into pubsub so subsequent
    # publishes for ``sess_id`` enqueue here via call_soon_threadsafe.
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
        "Cache-Control": "no-cache",
        # nginx hint — flush on every yield.
        "X-Accel-Buffering": "no",
    }
    return StreamingResponse(
        stream(), media_type="text/event-stream", headers=headers,
    )
