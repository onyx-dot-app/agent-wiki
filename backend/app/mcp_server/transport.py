"""JSON-RPC 2.0 dispatcher for the inbound MCP server.

Hand-rolled rather than via the ``mcp`` Python SDK. Phase 2 only needs
``initialize``, ``notifications/initialized``, ``ping``, and an empty
``tools/list``; pulling the SDK in would require an ASGI bridge for
Flask (the SDK is async-first), which is too much infra for this
slice. The doc's Phase-2 fallback path explicitly allows this:

  > If the SDK proves heavy or surprising, the fallback is to write a
  > thin JSON-RPC handler in ``app/mcp_server/transport.py`` and reuse
  > the SSE plumbing from ``app/api/chat.py``.

If Phase 5's resource-subscription work makes the streaming-on-POST
shape painful, swap to the SDK then. Until then, the surface is small
enough to keep here.

Spec reference (MCP 2025-03-26):
  https://spec.modelcontextprotocol.io/specification/2025-03-26/
"""

from __future__ import annotations

import logging
from typing import Any, cast

from app.auth import PermissionDenied, User
from app.mcp_server import resources as mcp_resources
from app.mcp_server import session as mcp_session
from app.mcp_server import tools as mcp_tools

log = logging.getLogger(__name__)

PROTOCOL_VERSION = "2025-03-26"
SERVER_NAME = "agent-wiki"
SERVER_VERSION = "0.1.0"

SERVER_INSTRUCTIONS = """\
Agent Wiki is a shared collaboration space for humans and agents, and the source of truth for status and progress on all ongoing projects. Documents are markdown organized as a file hierarchy — paths convey scope (e.g. `projects/<name>/`, `runbooks/`, `decisions/`).

CRITICAL: Before beginning any work, search the wiki for relevant context. Use `search_wiki` to look up topics, `read_doc` to fetch full pages, and `ask_nl_question` for fuzzy questions across the corpus. Reference the paths you used in your response so humans and other agents can verify and follow up.

As you progress, keep relevant pages up to date. Update at major checkpoints rather than on every step. Scratchpads and in-progress notes are welcome — communicating non-final progress is valuable when it might affect another agent's work. Add cross-links between related pages so the graph stays navigable. If your work produces a significant deliverable, ask the user whether they'd like a dedicated page for it — never create one proactively.

Documents change while you work. Other agents and humans may edit pages you depend on. If new information lands that affects your task, incorporate it rather than pressing on with stale context.

The wiki holds the team's critical knowledge — keep it current proactively so nothing important is lost, and prune or correct anything that's been invalidated. Remember that this wiki is intended for use by both humans and AI agents — keep it organized and free from bloat."""

# JSON-RPC 2.0 standard error codes
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603


class UnknownSessionError(Exception):
    """A request carried an ``Mcp-Session-Id`` the server doesn't recognize.

    Sessions are in-memory and don't survive a backend restart, so a stale
    id is the common case after a redeploy. The HTTP layer maps this to a
    404 so the client starts a fresh session — per the MCP Streamable HTTP
    spec, a 404 to a request bearing a session id is the signal to
    re-initialize. (Contrast with a request that omits the header entirely,
    which is a plain protocol error, not a recoverable stale session.)
    """

    def __init__(self, request_id: Any) -> None:
        self.request_id = request_id
        super().__init__("missing or invalid Mcp-Session-Id")

    def jsonrpc_error(self) -> dict[str, Any]:
        return _error(self.request_id, INVALID_REQUEST, "missing or invalid Mcp-Session-Id")


def dispatch(
    message: dict[str, Any],
    session_id: str | None,
    bearer_user: User,
) -> tuple[dict[str, Any] | None, str | None]:
    """Process one JSON-RPC message.

    Returns ``(response_dict_or_None, session_id_to_return)``.
      * For requests (``id`` present): response is the JSON-RPC reply.
      * For notifications (``id`` absent): response is ``None``; the
        transport returns HTTP 202 with no body.
      * The returned ``session_id`` is what the transport puts in the
        ``Mcp-Session-Id`` response header. ``initialize`` mints a fresh
        id; everything else echoes the incoming id.

    ``bearer_user`` is the user resolved by the transport layer's
    bearer auth (``app.auth.deps.require_bearer``) — threaded
    explicitly so this module has no implicit
    request-context dependency.
    """
    if message.get("jsonrpc") != "2.0":
        return _error(message.get("id"), INVALID_REQUEST, "missing jsonrpc=2.0"), session_id

    method = message.get("method")
    if not isinstance(method, str):
        return _error(message.get("id"), INVALID_REQUEST, "missing method"), session_id

    is_notification = "id" not in message
    request_id = message.get("id")
    raw_params = message.get("params")
    params: dict[str, Any] = (
        cast("dict[str, Any]", raw_params) if isinstance(raw_params, dict) else {}
    )

    try:
        return _route(method, params, request_id, is_notification, session_id, bearer_user)
    except UnknownSessionError:
        # Surfaced to the HTTP layer as a 404 so the client re-initializes.
        raise
    except Exception:
        log.exception("mcp transport: handler raised for method=%s", method)
        if is_notification:
            return None, session_id
        return _error(request_id, INTERNAL_ERROR, "internal error"), session_id


def _route(
    method: str,
    params: dict[str, Any],
    request_id: Any,
    is_notification: bool,
    session_id: str | None,
    bearer_user: User,
) -> tuple[dict[str, Any] | None, str | None]:
    if method == "initialize":
        if is_notification:
            # ``initialize`` is a request, not a notification, per spec.
            return None, session_id
        result, new_session_id = _handle_initialize(params, bearer_user)
        return _success(request_id, result), new_session_id

    sess = mcp_session.get(session_id)
    if sess is None:
        if is_notification:
            # Stale notification for a session we don't track; ignore.
            return None, session_id
        if session_id is None:
            # No session header at all — a plain protocol error, not a
            # recoverable stale session. Stays an HTTP-200 JSON-RPC error.
            return (
                _error(request_id, INVALID_REQUEST, "missing or invalid Mcp-Session-Id"),
                session_id,
            )
        # Header present but unknown/expired → 404 so the client re-inits.
        raise UnknownSessionError(request_id)

    if method == "notifications/initialized":
        mcp_session.mark_initialized(session_id)
        return None, session_id

    if not sess.initialized:
        if is_notification:
            return None, session_id
        return _error(request_id, INVALID_REQUEST, "session not initialized"), session_id

    if method == "ping":
        if is_notification:
            return None, session_id
        return _success(request_id, {}), session_id

    if method == "tools/list":
        if is_notification:
            return None, session_id
        return _success(request_id, {"tools": mcp_tools.list_for_mcp()}), session_id

    if method == "tools/call":
        if is_notification:
            return None, session_id
        return _handle_tools_call(sess, params, request_id), session_id

    if method == "resources/list":
        if is_notification:
            return None, session_id
        return _success(request_id, mcp_resources.list_resources(sess)), session_id

    if method == "resources/read":
        if is_notification:
            return None, session_id
        return _handle_resources_read(sess, params, request_id), session_id

    if method == "resources/subscribe":
        if is_notification:
            return None, session_id
        return _handle_resources_subscribe(sess, params, request_id), session_id

    if method == "resources/unsubscribe":
        if is_notification:
            return None, session_id
        return _handle_resources_unsubscribe(sess, params, request_id), session_id

    if is_notification:
        # Unknown notifications are silently ignored per JSON-RPC.
        return None, session_id
    return _error(request_id, METHOD_NOT_FOUND, f"unknown method: {method}"), session_id


def _handle_resources_read(
    sess: mcp_session.McpSession, params: dict[str, Any], request_id: Any
) -> dict[str, Any]:
    uri = params.get("uri")
    if not isinstance(uri, str) or not uri:
        return _error(request_id, INVALID_PARAMS, "resources/read: uri is required")
    try:
        return _success(request_id, mcp_resources.read_resource(sess, uri))
    except PermissionDenied as exc:
        return _success(
            request_id,
            {"contents": [], "isError": True, "error": exc.message},
        )
    except ValueError as exc:
        return _error(request_id, INVALID_PARAMS, str(exc))
    except FileNotFoundError as exc:
        return _error(request_id, INVALID_PARAMS, f"resource not found: {exc}")


def _handle_resources_subscribe(
    sess: mcp_session.McpSession, params: dict[str, Any], request_id: Any
) -> dict[str, Any]:
    uri = params.get("uri")
    if not isinstance(uri, str) or not uri:
        return _error(request_id, INVALID_PARAMS, "resources/subscribe: uri is required")
    try:
        return _success(request_id, mcp_resources.subscribe_resource(sess, uri))
    except PermissionDenied as exc:
        # Match how MCP errors at the resources surface look for callers
        # — INVALID_REQUEST with a forbidden message.
        return _error(request_id, INVALID_REQUEST, exc.message)
    except ValueError as exc:
        return _error(request_id, INVALID_PARAMS, str(exc))


def _handle_resources_unsubscribe(
    sess: mcp_session.McpSession, params: dict[str, Any], request_id: Any
) -> dict[str, Any]:
    uri = params.get("uri")
    if not isinstance(uri, str) or not uri:
        return _error(request_id, INVALID_PARAMS, "resources/unsubscribe: uri is required")
    try:
        return _success(request_id, mcp_resources.unsubscribe_resource(sess, uri))
    except ValueError as exc:
        return _error(request_id, INVALID_PARAMS, str(exc))


def _handle_initialize(
    _params: dict[str, Any],
    bearer_user: User,
) -> tuple[dict[str, Any], str]:
    """Mint a fresh session for the authenticated user, return the
    capabilities envelope. The session is created in the
    ``initialized=False`` state — the client must follow up with
    ``notifications/initialized`` to flip it to ``True``.
    """
    sess = mcp_session.create(bearer_user)

    return (
        {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {
                "tools": {"listChanged": False},
                "resources": {"subscribe": True, "listChanged": True},
            },
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            "instructions": SERVER_INSTRUCTIONS,
        },
        sess.id,
    )


def _handle_tools_call(
    sess: mcp_session.McpSession, params: dict[str, Any], request_id: Any
) -> dict[str, Any]:
    """Dispatch ``tools/call`` to the chat-agent registry via the MCP
    adapter. Application-level errors (auth, missing path, etc.) come
    back inside the result envelope with ``isError=true`` — JSON-RPC
    errors are reserved for protocol-level issues.
    """
    name = params.get("name")
    if not isinstance(name, str) or not name:
        return _error(request_id, INVALID_PARAMS, "tools/call: name is required")
    raw_args = params.get("arguments")
    arguments: dict[str, Any] = (
        cast("dict[str, Any]", raw_args) if isinstance(raw_args, dict) else {}
    )

    payload, is_error = mcp_tools.call_for_mcp(sess, name, arguments)
    return _success(
        request_id,
        {"content": mcp_tools.to_mcp_content(payload), "isError": is_error},
    )


def _success(request_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _error(request_id: Any, code: int, message: str, data: Any = None) -> dict[str, Any]:
    err: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    return {"jsonrpc": "2.0", "id": request_id, "error": err}
