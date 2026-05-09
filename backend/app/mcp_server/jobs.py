"""Async-job repo for the inbound MCP server.

Backs the ``update_doc_nl`` async tool: each call inserts one row, the
worker process updates ``status`` / ``result_json`` / ``error`` /
``finished_at`` as the LLM run completes. ``mcp_pubsub.publish_job_update``
fans state changes out over SSE to any session subscribed to
``job://<id>``.

Idempotency: when a caller passes ``idempotency_key`` (or the wrapper
computes one from ``user_id + path + instruction``), a retry returns
the existing job instead of enqueueing a duplicate. Enforced by the
partial unique index ``idx_mcp_jobs_idemp`` on
``(user_id, idempotency_key)`` — partial so unkeyed jobs don't
collide.

Worker-side debounce: ``find_recent_succeeded_for_user_path`` looks
back N seconds for a same-path commit; the worker uses it to skip the
LLM call when the wiki was just updated.
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import desc, select

from app.db.models import McpJob
from app.db.session import session

log = logging.getLogger(__name__)

KIND_UPDATE_DOC_NL = "update_doc_nl"
STATUS_PENDING = "pending"
STATUS_RUNNING = "running"
STATUS_SUCCEEDED = "succeeded"
STATUS_FAILED = "failed"

_TERMINAL_STATUSES = (STATUS_SUCCEEDED, STATUS_FAILED)


def _to_dict(j: McpJob) -> dict[str, Any]:
    return {
        "id": j.id,
        "user_id": j.user_id,
        "kind": j.kind,
        "status": j.status,
        "idempotency_key": j.idempotency_key,
        "payload": json.loads(j.payload_json),
        "result": json.loads(j.result_json) if j.result_json else None,
        "error": j.error,
        "created_at": j.created_at,
        "finished_at": j.finished_at,
    }


def _new_id() -> str:
    return "mjb_" + uuid.uuid4().hex[:14]


# --------------------------------------------------------------------------- #
# Read                                                                        #
# --------------------------------------------------------------------------- #


def get(job_id: str) -> dict[str, Any] | None:
    with session() as s:
        j = s.get(McpJob, job_id)
        return _to_dict(j) if j else None


def find_by_idempotency_key(user_id: str, key: str) -> dict[str, Any] | None:
    """Look up an existing job by user + key. Used by the enqueue path
    to dedupe retries before inserting a new row."""
    with session() as s:
        j = s.scalar(
            select(McpJob)
            .where(McpJob.user_id == user_id)
            .where(McpJob.idempotency_key == key)
        )
        return _to_dict(j) if j else None


def find_recent_succeeded_for_user_path(
    user_id: str, path: str, within_seconds: int
) -> dict[str, Any] | None:
    """Most-recent succeeded ``update_doc_nl`` job for this (user, path)
    that committed within the last ``within_seconds`` seconds. Returns
    ``None`` if nothing in the window committed. Used by the worker as
    a server-side debounce against a chatty agent.

    "Committed" means the job's ``result["committed"]`` is true — a
    succeeded job that decided ``no_change`` doesn't suppress the next
    attempt.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=within_seconds)
    cutoff_str = cutoff.strftime("%Y-%m-%d %H:%M:%S")
    with session() as s:
        # Filter on JSON string match for path. Cheap because the
        # candidate set is already narrow (recent rows for one user).
        rows = s.scalars(
            select(McpJob)
            .where(McpJob.user_id == user_id)
            .where(McpJob.kind == KIND_UPDATE_DOC_NL)
            .where(McpJob.status == STATUS_SUCCEEDED)
            .where(McpJob.finished_at >= cutoff_str)
            .order_by(desc(McpJob.finished_at))
        ).all()
    for row in rows:
        try:
            payload: dict[str, Any] = json.loads(row.payload_json)
            result: dict[str, Any] = (
                json.loads(row.result_json) if row.result_json else {}
            )
        except (TypeError, ValueError):
            continue
        if payload.get("path") == path and result.get("committed"):
            return _to_dict(row)
    return None


# --------------------------------------------------------------------------- #
# Write                                                                       #
# --------------------------------------------------------------------------- #


def create(
    *,
    user_id: str,
    kind: str,
    payload: dict[str, Any],
    idempotency_key: str | None,
) -> dict[str, Any]:
    """Insert a new ``pending`` job. Caller is expected to have
    already checked ``find_by_idempotency_key`` and shortcut-returned
    the existing row when present — this function does not dedupe;
    if the caller skipped that check and the unique index trips, the
    integrity error is re-raised so they can retry the lookup."""
    job_id = _new_id()
    with session() as s:
        s.add(
            McpJob(
                id=job_id,
                user_id=user_id,
                kind=kind,
                status=STATUS_PENDING,
                idempotency_key=idempotency_key,
                payload_json=json.dumps(payload),
            )
        )
    log.info(
        "mcp job created id=%s user=%s kind=%s key=%s",
        job_id, user_id, kind, idempotency_key,
    )
    return _to_dict_by_id(job_id)


def mark_running(job_id: str) -> None:
    with session() as s:
        j = s.get(McpJob, job_id)
        if j is not None:
            j.status = STATUS_RUNNING


def mark_succeeded(job_id: str, result: dict[str, Any]) -> dict[str, Any]:
    with session() as s:
        j = s.get(McpJob, job_id)
        if j is None:
            raise ValueError(f"job {job_id!r} not found")
        j.status = STATUS_SUCCEEDED
        j.result_json = json.dumps(result)
        j.finished_at = _now_text()
    return _to_dict_by_id(job_id)


def mark_failed(job_id: str, error: str, result: dict[str, Any] | None = None) -> dict[str, Any]:
    with session() as s:
        j = s.get(McpJob, job_id)
        if j is None:
            raise ValueError(f"job {job_id!r} not found")
        j.status = STATUS_FAILED
        j.error = error
        if result is not None:
            j.result_json = json.dumps(result)
        j.finished_at = _now_text()
    return _to_dict_by_id(job_id)


# --------------------------------------------------------------------------- #
# Helpers                                                                     #
# --------------------------------------------------------------------------- #


def _to_dict_by_id(job_id: str) -> dict[str, Any]:
    """Re-read a row through a fresh session — used after a write so
    the returned dict reflects committed state (server defaults like
    ``created_at`` materialize on flush, not on the in-memory ORM
    object)."""
    out = get(job_id)
    assert out is not None, f"job {job_id!r} disappeared right after write"
    return out


def _now_text() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def is_terminal(status: str) -> bool:
    return status in _TERMINAL_STATUSES
