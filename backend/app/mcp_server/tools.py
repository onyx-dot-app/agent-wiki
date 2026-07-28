"""Tool surface exposed by the inbound MCP server.

The handlers themselves live in ``app/llm/agents/tools/`` — the same
registry the in-process chat agent uses. This module is the MCP-facing
adapter:

  * ``MCP_ALLOWED_TOOLS`` — explicit allow-list of tool names. Walking
    the chat-agent registry blindly would expose tools that don't make
    sense over MCP yet (write tools land in Phase 4; bash / web tools
    are out of scope for v0). A new tool only becomes MCP-callable
    after its name is added here.

  * ``list_for_mcp()`` — translates the chat-agent JSON specs into
    MCP's ``tools/list`` shape (``input_schema`` → ``inputSchema``).

  * ``call_for_mcp()`` — dispatches into the chat-agent registry and
    translates the handler's return shape into MCP's
    ``{content, isError}`` envelope.

Design: ``local_data/wiki/mcp-server/mcp-server.md`` (Phase 3+).
"""
from __future__ import annotations

import json
import logging
from typing import Any, cast

import hashlib

from app.auth import PermissionDenied, require_can
from app.llm.agents.tools.errors import ToolError
from app.wiki import agent_activity, git as wiki_git, utils as wiki_utils
from app.llm.agents.tools import TOOL_SPECS, dispatch as registry_dispatch
from app.mcp_server import jobs as mcp_jobs
from app.mcp_server import pubsub as mcp_pubsub
from app.mcp_server.session import McpSession
from app.tasks.wiki_update import agent_update_document_nl

log = logging.getLogger(__name__)

# Phases 3 + 4 — sync tools dispatched directly into the chat-agent
# registry. Phase 6 adds the async ``update_doc_nl`` (handled below in
# ``_call_async_nl_update``); listed here so it shows up in
# ``tools/list`` but routed away from the sync dispatch path. Bash /
# web tools stay off-list (out of scope for v0).
MCP_ALLOWED_TOOLS: frozenset[str] = frozenset(
    {
        # Read
        "read_doc",
        "search_wiki",
        "search_comments",
        "list_history",
        "list_templates",
        "ask_nl_question",
        # Write — sync
        "edit_doc",
        "multi_edit",
        "write_doc",
        "apply_patch",
        "move_path",
        "delete_doc",
        "create_directory",
        "add_comment",
        "reply_comment",
        "resolve_comment",
        "set_update_policy",
        # Write — async
        "update_doc_nl",
    }
)

# Tools that need the MCP-side async wrapper instead of the sync
# chat-agent handler. ``tools/call`` routes these through
# ``_call_async_nl_update`` and family.
MCP_ASYNC_TOOLS: frozenset[str] = frozenset({"update_doc_nl"})


def list_for_mcp() -> list[dict[str, Any]]:
    """Return the MCP-shape tool list — only tools in ``MCP_ALLOWED_TOOLS``,
    with ``input_schema`` renamed to ``inputSchema`` per MCP spec.
    """
    out: list[dict[str, Any]] = []
    for spec in TOOL_SPECS:
        name = spec.get("name")
        if name not in MCP_ALLOWED_TOOLS:
            continue
        out.append(
            {
                "name": name,
                "description": spec.get("description", ""),
                "inputSchema": spec.get("input_schema", {"type": "object"}),
            }
        )
    return out


def _maybe_auto_subscribe(
    sess: McpSession,
    name: str,
    arguments: dict[str, Any],
    payload: dict[str, Any],
    is_error: bool,
) -> None:
    """``read_doc`` with ``subscribe=true`` (the default) auto-registers
    the session for ``wiki:///<path>`` so future commits push
    notifications. HEAD-only — historical reads are explicitly excluded
    because subscribing to a sha is meaningless.
    """
    if name != "read_doc" or is_error:
        return
    subscribe = arguments.get("subscribe", True)
    if subscribe is False:
        return
    if not payload.get("is_head"):
        return
    rel = payload.get("path")
    if not isinstance(rel, str) or not rel:
        return
    # Local import — pubsub depends on this module transitively.

    mcp_pubsub.subscribe_doc(sess.id, rel)


def call_for_mcp(
    sess: McpSession, name: str, arguments: dict[str, Any]
) -> tuple[dict[str, Any], bool]:
    """Dispatch a tool call on behalf of an MCP session.

    Returns ``(payload_dict, is_error)``. The transport layer wraps this
    into MCP's ``{content: [{type, text}], isError}`` shape.

    Application-level errors (file not found, permission denied,
    invalid path, ``stale_base``) come back as ``({"error": "..."},
    True)`` — same shape the chat agent already produces, so the MCP
    wrapper doesn't need a custom translation table.

    Every successful payload also carries a ``stale_paths`` array —
    paths the session has subscribed to that have changed since the
    last tool call. Phase 4 always returns ``[]`` here because
    subscriptions don't exist yet; the field becomes meaningful in
    Phase 5 when ``resources/subscribe`` lands. We add it now so the
    contract is stable from day one and clients don't have to special-
    case its later appearance.
    """
    if name not in MCP_ALLOWED_TOOLS:
        return {"error": f"unknown tool: {name}", "stale_paths": []}, True

    if name in MCP_ASYNC_TOOLS:
        return _call_async_nl_update(sess, arguments)

    try:
        result = registry_dispatch(name, arguments)
    except Exception as exc:
        log.exception("mcp tool dispatch raised name=%s", name)
        return {"error": f"internal error: {exc}", "stale_paths": []}, True

    is_error = isinstance(result, dict) and "error" in result
    payload: dict[str, Any] = (
        cast("dict[str, Any]", result) if isinstance(result, dict) else {"result": result}
    )
    _maybe_auto_subscribe(sess, name, arguments, payload, is_error)
    payload.setdefault("stale_paths", _compute_stale_paths(sess))
    guidance = _guidance_for(payload)
    if guidance is not None:
        payload["guidance"] = guidance
    return payload, is_error


def _call_async_nl_update(
    sess: McpSession, arguments: dict[str, Any]
) -> tuple[dict[str, Any], bool]:
    """MCP-side wrapper for ``update_doc_nl``.

    Validates inputs, enforces ACL at *enqueue* time, dedupes via the
    idempotency key, inserts the ``mcp_jobs`` row, enqueues the
    worker task, and auto-subscribes the calling session to
    ``job://<id>`` so the SSE stream pushes status changes without a
    separate ``resources/subscribe`` call.

    Returns ``({job_id, status_uri, status, ...}, False)`` on enqueue,
    ``({error, ...}, True)`` on validation / ACL failure.
    """
    # Local imports — these modules pull in the queue + worker which
    # we don't want loaded at module-import time for tools.py callers
    # that just want list_for_mcp.


    # ---- Validate ----
    raw_path = arguments.get("path")
    instruction = arguments.get("instruction")
    idempotency_key = arguments.get("idempotency_key")

    try:
        rel = wiki_utils.validate_doc_path(raw_path)
    except ToolError as exc:
        return {"error": str(exc), "stale_paths": _compute_stale_paths(sess)}, True
    if not isinstance(instruction, str) or not instruction.strip():
        return (
            {"error": "instruction is required (non-empty string)",
             "stale_paths": _compute_stale_paths(sess)},
            True,
        )
    if idempotency_key is not None and not isinstance(idempotency_key, str):
        return (
            {"error": "idempotency_key must be a string when provided",
             "stale_paths": _compute_stale_paths(sess)},
            True,
        )

    if not wiki_utils.file_exists(rel):
        return (
            {"error": f"file not found: {rel}",
             "stale_paths": _compute_stale_paths(sess)},
            True,
        )

    # ---- ACL gate at enqueue (worker can't re-check) ----
    try:
        require_can("write", rel)
    except PermissionDenied as exc:
        return {"error": str(exc), "stale_paths": _compute_stale_paths(sess)}, True

    # ---- Idempotency dedupe ----
    if not idempotency_key:
        # Default: hash of (user_id + path + instruction). Lets retries
        # of the same instruction collapse without the agent having to
        # mint its own key. Truncated to 32 hex chars — enough for
        # collision avoidance at human scale.
        idempotency_key = hashlib.sha256(
            f"{sess.user_id}|{rel}|{instruction.strip()}".encode("utf-8")
        ).hexdigest()[:32]

    existing = mcp_jobs.find_by_idempotency_key(sess.user_id, idempotency_key)
    if existing is not None:
        # Auto-subscribe so the agent's SSE stream still gets future
        # status pushes for this in-flight job, even though we didn't
        # mint a new row.
        mcp_pubsub.subscribe_job(sess.id, existing["id"])
        return (
            _job_response(sess, existing, deduplicated=True),
            False,
        )

    # ---- Insert + enqueue ----
    head_sha = wiki_git.head_sha_for_path(rel)
    # Capture the per-key agent identity here so the worker can rebind
    # it before commit_and_fan_out — the bearer ContextVar is gone by
    # the time the worker runs.

    payload: dict[str, Any] = {
        "path": rel,
        "instruction": instruction.strip(),
        "head_at_enqueue": head_sha,
        "agent_name": agent_activity.agent_name_var.get(),
    }
    job = mcp_jobs.create(
        user_id=sess.user_id,
        kind=mcp_jobs.KIND_UPDATE_DOC_NL,
        payload=payload,
        idempotency_key=idempotency_key,
    )
    mcp_pubsub.subscribe_job(sess.id, job["id"])
    agent_update_document_nl(job["id"])

    return _job_response(sess, job, deduplicated=False), False


def _job_response(
    sess: McpSession, job: dict[str, Any], *, deduplicated: bool
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "job_id": job["id"],
        "status_uri": f"job://{job['id']}",
        "status": job["status"],
        "deduplicated": deduplicated,
        "stale_paths": _compute_stale_paths(sess),
    }
    if job.get("result") is not None:
        out["result"] = job["result"]
    if job.get("error") is not None:
        out["error_detail"] = job["error"]
    return out


def _compute_stale_paths(sess: McpSession) -> list[str]:
    """Paths the session is subscribed to that have a pending push.

    Implemented as a non-destructive peek at the session's pub-sub
    sync queue: read every queued notification, collect the URIs that
    map to ``wiki:///<path>``, and (importantly) put the notifications
    back so a later SSE open still ships them. The sync queue only
    accumulates while no SSE writer is registered (with a live stream,
    notifications go straight to the async queue), so this is the
    poll-based fallback for clients that aren't holding a stream open
    — empty in the steady state for a connected, attentive client.
    """

    q = mcp_pubsub.queue_for(sess.id)
    drained: list[Any] = []
    paths: list[str] = []
    try:
        while True:
            notif = q.get_nowait()
            drained.append(notif)
            params = notif.params or {}
            uri = params.get("uri")
            if isinstance(uri, str) and uri.startswith("wiki:///"):
                rel = uri[len("wiki:///"):]
                if rel and rel not in paths:
                    paths.append(rel)
    except Exception:
        # ``queue.Empty`` is the expected exit; any other exception we
        # treat the same — preserve whatever we drained, return what
        # we found.
        pass
    finally:
        for notif in drained:
            try:
                # ``put_nowait`` — the sync queue is bounded, and a
                # concurrent publish may have refilled it while we were
                # peeking. Dropping the put-back is fine: these are
                # re-read hints and the caller just received the path
                # in ``stale_paths``.
                q.put_nowait(notif)
            except Exception:
                break
    return paths


def _guidance_for(payload: dict[str, Any]) -> str | None:
    """Short, actionable next-step hint for a tool result — only when there's
    something concrete to act on.

    Returns ``None`` when nothing needs saying, so results don't carry a nudge
    on every call (a constant nudge just trains clients to ignore the field).
    The hint rides a tool call the agent already made — the one place an MCP
    server can steer the next action without a dedicated trigger.
    """
    hints: list[str] = []

    stale = payload.get("stale_paths")
    if isinstance(stale, list) and stale:
        joined = ", ".join(str(p) for p in cast("list[object]", stale))
        hints.append(
            f"Pages you relied on changed since you last read them ({joined}); "
            "re-read them before continuing."
        )

    broken = payload.get("broken_links")
    if isinstance(broken, list) and broken:
        hints.append("This edit left broken markdown links — fix or remove them.")

    if not hints:
        return None
    return " ".join(hints)


def to_mcp_content(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Wrap a handler payload in MCP's ``content`` array.

    Default representation is a single ``text`` block carrying the
    JSON-stringified payload. Tools that produce binary data (none yet)
    would extend this to ``image`` / ``resource`` blocks per the MCP
    spec.
    """
    return [{"type": "text", "text": json.dumps(payload)}]
