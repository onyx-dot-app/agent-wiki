"""MCP resources: ``wiki:///<path>`` and ``job://<id>`` URIs the
client can list, read, and subscribe to.

Surface:

  * ``resources/list``      — every ``.md`` page the user can read,
                              filtered through ``acl.filter_paths_in_python``,
                              plus this user's still-active async jobs
                              (``mcp_jobs`` rows that haven't terminated
                              or that terminated recently).
  * ``resources/read``      — body of a single page at HEAD, or the
                              JSON-serialized state of a job. Gated by
                              ``require_can("read", path)`` for pages;
                              jobs are scoped to their owner.
  * ``resources/subscribe`` — start receiving
                              ``notifications/resources/updated`` for a
                              page or job. Refused if the user can't
                              access the resource.
  * ``resources/unsubscribe`` — stop receiving them.

Pushes themselves come from ``app.mcp_server.pubsub`` and are delivered
over the SSE stream on ``GET /api/mcp``.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from app.auth import require_can
from app.mcp_server import jobs as mcp_jobs
from app.mcp_server import pubsub as mcp_pubsub
from app.mcp_server.session import McpSession
from app.wiki import acl, git as wiki_git
from app.wiki.filesystem import safe_rel_path

log = logging.getLogger(__name__)

URI_PREFIX = "wiki:///"
JOB_URI_PREFIX = "job://"


def _strip_uri(uri: str) -> str:
    """Pull the rel-path out of ``wiki:///foo/bar.md``. Raises ValueError
    on malformed input — the transport translates that into JSON-RPC
    INVALID_PARAMS."""
    if not uri.startswith(URI_PREFIX):
        raise ValueError(f"unsupported resource URI: {uri!r}")
    return safe_rel_path(uri[len(URI_PREFIX):])


def _strip_job_uri(uri: str) -> str:
    """Pull the job id out of ``job://<id>``."""
    if not uri.startswith(JOB_URI_PREFIX):
        raise ValueError(f"unsupported job URI: {uri!r}")
    job_id = uri[len(JOB_URI_PREFIX):].strip()
    if not job_id:
        raise ValueError("empty job id")
    return job_id


def list_resources(sess: McpSession) -> dict[str, Any]:
    """``resources/list`` — every readable ``.md`` page in the wiki.

    Tree-walks the working copy via ``git ls-files`` (same source the
    bootstrap and listing endpoints use), then filters through ACL in
    Python. Flat list — no resource templates yet (out of scope until
    we hit the few-thousand-doc breakpoint).
    """
    all_paths = [p for p in wiki_git.tree_paths_at("HEAD") if p.endswith(".md")]
    visible = acl.filter_paths_in_python(sess.user_id, sess.is_admin, all_paths)
    return {
        "resources": [
            {
                "uri": f"{URI_PREFIX}{rel}",
                "name": rel,
                "mimeType": "text/markdown",
            }
            for rel in visible
        ]
    }


def read_resource(sess: McpSession, uri: str) -> dict[str, Any]:
    """``resources/read``.

    For ``wiki:///<path>``: body at HEAD; ``require_can`` raises
    ``PermissionDenied`` if the user lacks read access.

    For ``job://<id>``: JSON-serialized job state. Jobs are scoped to
    their owner — a job belonging to another user reads as if it
    didn't exist (no info leak through error messages).
    """
    if uri.startswith(JOB_URI_PREFIX):
        job_id = _strip_job_uri(uri)
        job = mcp_jobs.get(job_id)
        if job is None or job["user_id"] != sess.user_id:
            raise FileNotFoundError(uri)
        return {
            "contents": [
                {
                    "uri": uri,
                    "mimeType": "application/json",
                    "text": json.dumps(_job_public_view(job)),
                }
            ]
        }

    rel = _strip_uri(uri)
    require_can("read", rel)
    body = wiki_git.read_file(rel, ref="HEAD")
    return {
        "contents": [
            {
                "uri": uri,
                "mimeType": "text/markdown",
                "text": body,
            }
        ]
    }


def subscribe_resource(sess: McpSession, uri: str) -> dict[str, Any]:
    """``resources/subscribe`` — record interest so future commits or
    job-status changes push notifications.

    For ``wiki:///<path>``: refused (``forbidden``) if the user can't
    read. Per-subscriber recheck in ``pubsub._should_deliver`` is the
    second layer for revocations mid-session.

    For ``job://<id>``: refused (``forbidden``) if the job belongs to
    a different user.
    """
    if uri.startswith(JOB_URI_PREFIX):
        job_id = _strip_job_uri(uri)
        job = mcp_jobs.get(job_id)
        if job is None or job["user_id"] != sess.user_id:
            from app.auth import PermissionDenied  # noqa: PLC0415

            raise PermissionDenied(f"forbidden: cannot subscribe to {uri}")
        mcp_pubsub.subscribe_job(sess.id, job_id)
        return {}

    rel = _strip_uri(uri)
    require_can("read", rel)
    mcp_pubsub.subscribe_doc(sess.id, rel)
    return {}


def unsubscribe_resource(sess: McpSession, uri: str) -> dict[str, Any]:
    if uri.startswith(JOB_URI_PREFIX):
        job_id = _strip_job_uri(uri)
        mcp_pubsub.unsubscribe_job(sess.id, job_id)
        return {}
    rel = _strip_uri(uri)
    mcp_pubsub.unsubscribe_doc(sess.id, rel)
    return {}


def _job_public_view(job: dict[str, Any]) -> dict[str, Any]:
    """Strip internal fields off the job before sending to the client.
    The user_id is implicit (the caller is the owner); the raw
    payload_json is exposed as ``payload`` for transparency."""
    return {
        "id": job["id"],
        "kind": job["kind"],
        "status": job["status"],
        "payload": job["payload"],
        "result": job["result"],
        "error": job["error"],
        "created_at": job["created_at"],
        "finished_at": job["finished_at"],
    }


__all__ = [
    "list_resources",
    "read_resource",
    "subscribe_resource",
    "unsubscribe_resource",
    "URI_PREFIX",
]
