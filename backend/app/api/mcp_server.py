"""Inbound MCP server transport — endpoints at ``POST /api/mcp`` and
``GET /api/mcp``.

External coding agents (Claude Code, Cursor, Codex) connect here over
JSON-RPC 2.0 with a bearer token. The hard work — auth, session
state, JSON-RPC dispatch, pub-sub — lives in ``app.mcp_server``; this
module is the thin Flask glue.

POST is per-call request/response. GET is the long-lived SSE stream
for server-initiated frames (``notifications/resources/updated``,
``notifications/resources/list_changed``). Both wear the same bearer
auth.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Iterator, cast

from flask import Blueprint, Response, jsonify, request, stream_with_context

from app.mcp_server import pubsub as mcp_pubsub
from app.mcp_server import session as mcp_session
from app.mcp_server.auth import bearer_required
from app.mcp_server.transport import dispatch

log = logging.getLogger(__name__)

bp = Blueprint("mcp_server", __name__)

SESSION_HEADER = "Mcp-Session-Id"

# How long the SSE writer parks waiting for a notification before
# checking liveness / yielding a comment. Keeps the connection from
# looking idle to nginx without requiring a separate keepalive thread.
_SSE_HEARTBEAT_SECONDS = 15.0


@bp.post("")
@bearer_required
def transport_post():
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        return jsonify(error="request body must be a JSON-RPC object"), 400

    incoming = request.headers.get(SESSION_HEADER)
    response, outgoing = dispatch(cast("dict[str, Any]", body), incoming)

    headers: dict[str, str] = {}
    if outgoing:
        headers[SESSION_HEADER] = outgoing

    if response is None:
        # Notification — JSON-RPC convention: no body. 202 Accepted is
        # the MCP-recommended status for notification POSTs.
        return Response(status=202, headers=headers)

    return jsonify(response), 200, headers


@bp.get("")
@bearer_required
def transport_sse():
    """Open the long-lived SSE stream for server-initiated frames.

    Pre-conditions:
      * Bearer token (handled by ``@bearer_required``).
      * ``Mcp-Session-Id`` header pointing at an *initialized* session
        owned by the bearer's user — the client must have completed
        ``initialize`` + ``notifications/initialized`` first.

    On disconnect the generator's ``finally`` clause runs
    ``mcp_session.drop(session_id)``, which clears the session row
    *and* its pub-sub subscriptions and queue (see
    ``app.mcp_server.pubsub.forget``).
    """
    sess_id = request.headers.get(SESSION_HEADER)
    if not sess_id:
        return jsonify(error=f"missing {SESSION_HEADER} header"), 400
    sess = mcp_session.get(sess_id)
    if sess is None or not sess.initialized:
        return jsonify(error="session not initialized"), 400

    from flask import g

    bearer_user = g.user
    if sess.user_id != bearer_user.id:
        # The bearer resolves to user A but the supplied session id was
        # minted for user B — almost certainly an attempted hijack. Refuse.
        return jsonify(error="session does not belong to this bearer"), 403

    def stream() -> Iterator[str]:
        try:
            while True:
                notif = mcp_pubsub.drain_blocking(sess_id, _SSE_HEARTBEAT_SECONDS)
                if notif is None:
                    # Heartbeat — comment line keeps proxies from idling
                    # the connection without showing up in the JSON-RPC
                    # event stream.
                    yield ": keepalive\n\n"
                    continue
                frame: dict[str, Any] = {
                    "jsonrpc": "2.0",
                    "method": notif.method,
                    "params": notif.params,
                }
                yield f"data: {json.dumps(frame)}\n\n"
        except GeneratorExit:
            # Client disconnected — Flask raises this when the
            # response is closed. Fall through to the finally block.
            raise
        finally:
            mcp_session.drop(sess_id)
            log.info("mcp sse stream closed session=%s", sess_id)

    headers = {
        "Cache-Control": "no-cache",
        # Same nginx hint the chat stream uses — flush on every yield.
        "X-Accel-Buffering": "no",
    }
    return Response(
        stream_with_context(stream()),
        mimetype="text/event-stream",
        headers=headers,
    )
