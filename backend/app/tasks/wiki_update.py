"""LLM doc-reconciliation tasks — the "wikis stay current" loop.

Tasks in this module run on the ``documents_queue`` queue — the slow,
LLM-bound queue dedicated to running the document-updater agent against a
wiki page. Each task may make a full LLM call and produce a new commit, so
we keep this work off the indexer / trigger queues to prevent provider
slowness from cascading into search staleness or delayed trigger fires.

After a successful commit, these tasks re-enqueue ``index_path`` (on
``lightweight_maintenance_queue``) and ``fan_out_trigger_eval`` (on
``triggers_queue``) so the side effects fan out exactly like a human edit.

v0 hands the agent a single doc and the new payload; later versions can
scale this with batching, dedup, etc. Watch the cost — every connector
update triggering a full LLM pass is expensive.

See ``app/tasks/queues.py`` for the queue rationale.
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any

from app.config import CONFIG
from app.ingest import search as ingest_search
from app.ingest.source_tiers import is_filtered
from app.llm.agents import wiki_updater
from app.llm.agents.wiki_updater import IRRELEVANT_SENTINEL
from app.llm.agents.tools import _doc_helpers as h
from app.llm.errors import LLMError
from app.mcp_server import jobs as mcp_jobs
from app.mcp_server import pubsub as mcp_pubsub
from app.auth import UserMissingError, load_user, set_current_user
from app.tasks.queues import documents_queue
from app.wiki import agent_activity, git as wiki_git, notify as wiki_notify

_INGEST_AUTHOR = "Onyx Ingest <ingest@agent-wiki>"

log = logging.getLogger(__name__)

# Server-side debounce: skip the LLM call when the same (user, path) was
# successfully committed within this many seconds. Cheap insurance
# against a chatty agent. Read once at module load — workers restart on
# config changes today.
_DEBOUNCE_SECONDS = int(os.environ.get("MCP_NL_DEBOUNCE_SECONDS", "30"))

# Hard cap on the commit-message length we record on disk; the
# instruction may be long, but we only want a quick handle.
_COMMIT_MESSAGE_MAX = 80


@documents_queue.task()
def update_document_from_payload(doc_id: str, source: str, payload: dict[str, Any]) -> None:
    log.info("update_document_from_payload doc_id=%s source=%s", doc_id, source)
    # TODO:
    #   1. Load current doc body from git (app.wiki.git.read_file).
    #   2. Call app.llm.agents.wiki_updater.process_instruction(wiki_path, body, payload, source).
    #   3. If the agent produced a new body, commit it (app.wiki.git.commit_file).
    #   4. Enqueue index_path on lightweight_maintenance_queue.
    #   5. Enqueue fan_out_trigger_eval on triggers_queue for doc + parent dirs.
    raise NotImplementedError


@documents_queue.task()
def agent_update_document_nl(job_id: str) -> None:
    """Worker side of the inbound MCP ``update_doc_nl`` async tool.

    Phases:

      1. Load the job row.
      2. Reconstitute ``g.user`` from ``mcp_jobs.user_id`` so every
         downstream helper sees the right principal — same seam the
         POST handler uses.
      3. ``base_sha`` recheck — HEAD might have moved between enqueue
         and run. Fail with ``stale_base`` if so.
      4. Server-side debounce — if a same-(user, path) job committed
         within ``MCP_NL_DEBOUNCE_SECONDS``, succeed with
         ``committed=false reason=debounced``.
      5. Run the document-updater agent.
      6. ``NO_CHANGE`` → succeed with ``committed=false``.
         New body → ``commit_and_fan_out`` → succeed with the sha.
         Exception → fail with the error code.
      7. Publish the terminal status to ``job://<id>`` subscribers.
    """
    job = mcp_jobs.get(job_id)
    if job is None:
        log.warning("agent_update_document_nl: job %s not found, dropping", job_id)
        return

    payload = job["payload"]
    rel = payload.get("path")
    instruction = payload.get("instruction") or ""
    base_sha = payload.get("base_sha")
    agent_name = payload.get("agent_name")

    try:
        user = load_user(job["user_id"])
        agent_token = agent_activity.agent_name_var.set(agent_name)
        try:
            with set_current_user(user):
                mcp_jobs.mark_running(job_id)
                _run_inner(job_id, rel, instruction, base_sha)
        finally:
            agent_activity.agent_name_var.reset(agent_token)
    except UserMissingError as exc:
        log.warning("agent_update_document_nl: user %s missing for job %s", exc.user_id, job_id)
        mcp_jobs.mark_failed(job_id, error="user_missing")
        mcp_pubsub.publish_job_update(job_id, "failed")
    except Exception:
        log.exception("agent_update_document_nl crashed job=%s", job_id)
        mcp_jobs.mark_failed(job_id, error="internal_error")
        mcp_pubsub.publish_job_update(job_id, "failed")


def _run_inner(job_id: str, rel: str, instruction: str, base_sha: str | None) -> None:
    """Inside the worker's user context. Splits out so the outer
    function can wrap exceptions uniformly."""
    if not rel:
        mcp_jobs.mark_failed(job_id, error="invalid_path")
        mcp_pubsub.publish_job_update(job_id, "failed")
        return

    if not h.file_exists(rel):
        mcp_jobs.mark_failed(job_id, error=f"file not found: {rel}")
        mcp_pubsub.publish_job_update(job_id, "failed")
        return

    head_sha = wiki_git.head_sha_for_path(rel)
    if base_sha and base_sha != head_sha:
        mcp_jobs.mark_failed(
            job_id,
            error="stale_base",
            result={
                "base_sha": base_sha,
                "current_sha": head_sha or "",
            },
        )
        mcp_pubsub.publish_job_update(job_id, "failed")
        return

    debounced = mcp_jobs.find_recent_succeeded_for_user_path(
        user_id=_current_user_id(), path=rel, within_seconds=_DEBOUNCE_SECONDS
    )
    if debounced is not None:
        mcp_jobs.mark_succeeded(
            job_id,
            result={
                "committed": False,
                "reason": "debounced",
                "debounce_window_seconds": _DEBOUNCE_SECONDS,
                "previous_job_id": debounced["id"],
                "sha": head_sha,
            },
        )
        mcp_pubsub.publish_job_update(job_id, "succeeded")
        return

    old_body = h.read_existing(rel)
    try:
        new_body = wiki_updater.process_instruction(
            wiki_path=rel,
            current_body=old_body,
            payload={"instruction": instruction},
            source="update_doc_nl",
        )
    except LLMError as exc:
        mcp_jobs.mark_failed(job_id, error=f"llm_error: {exc}")
        mcp_pubsub.publish_job_update(job_id, "failed")
        return

    if new_body is None or new_body == old_body:
        mcp_jobs.mark_succeeded(
            job_id,
            result={"committed": False, "reason": "no_change", "sha": head_sha},
        )
        mcp_pubsub.publish_job_update(job_id, "succeeded")
        return

    try:
        sha = h.commit_and_fan_out(
            rel,
            new_body,
            f"Doc update: {instruction[:_COMMIT_MESSAGE_MAX]}",
            change_kind="edit",
        )
    except h.ToolError as exc:
        mcp_jobs.mark_failed(job_id, error=str(exc))
        mcp_pubsub.publish_job_update(job_id, "failed")
        return

    mcp_jobs.mark_succeeded(
        job_id,
        result={
            "committed": True,
            "sha": sha,
            "diff": h.unified_diff(old_body, new_body, rel),
            "broken_links": h.broken_links(rel, new_body),
        },
    )
    mcp_pubsub.publish_job_update(job_id, "succeeded")


def _current_user_id() -> str:
    """Read the user id off the ContextVar — bound by
    ``set_current_user(...)`` in the outer task. Asserts rather than
    degrades because every caller is inside that context manager."""
    from app.auth import current_user

    user = current_user()
    assert user is not None, "_run_inner must execute inside set_current_user(...)"
    return user.id


# Quiet unused-import warning when Phase 7 wires this elsewhere — keeps
# the symbol on hand for future callers.
_ = CONFIG


@documents_queue.task()
def process_pushed_document(push: dict[str, Any]) -> None:
    """Reconcile a document pushed from an external system into the wiki.

    ``push`` is the validated payload from POST /api/wiki/ingest. Shape:
    ``{content, title?, source_type?, source_document_id?, metadata?,
       updated_at?, diff?}``.

    Pipeline:
      1. Drop filtered sources silently.
      2. BM25 search + title boost + score threshold to find candidates.
      3. Walk candidates (best score first); call updater LLM on each.
         - new body   → commit + fan-out, reset irrelevant counter
         - NO_CHANGE  → skip commit, reset irrelevant counter
         - IRRELEVANT → increment counter; stop when ≥ INGEST_IRRELEVANT_STOP_N
    """
    source_type = push.get("source_type")
    title = push.get("title")
    content: str = push.get("content") or ""
    doc_id = push.get("source_document_id") or push.get("title") or "unknown"

    log.info(
        "process_pushed_document source_type=%s title=%s len=%d",
        source_type, title, len(content),
    )

    if is_filtered(source_type):
        log.debug("process_pushed_document: filtered source %s, dropping", source_type)
        return

    t_start = time.monotonic()
    hits = ingest_search.candidates(content, title)
    if not hits:
        log.info("process_pushed_document: no BM25 candidates above threshold, doc_id=%s", doc_id)
        return

    source_label = source_type or "external"
    _meta: dict[str, Any] = push.get("metadata") or {}
    url: str = str(_meta.get("url") or "")

    consecutive_irrelevant = 0
    llm_calls = 0
    irrelevant = 0
    committed = 0
    stopped_early = False

    for hit in hits:
        try:
            current_body = wiki_git.read_file(hit.path)
        except Exception:
            log.debug("process_pushed_document: skipping unreadable %s", hit.path)
            continue

        try:
            result = wiki_updater.reconcile_document(
                wiki_path=hit.path,
                current_body=current_body,
                source=source_label,
                title=title,
                url=url,
                content=content,
            )
            llm_calls += 1
        except LLMError:
            log.warning("process_pushed_document: LLM error for %s, skipping", hit.path, exc_info=True)
            continue

        if result == IRRELEVANT_SENTINEL:
            irrelevant += 1
            consecutive_irrelevant += 1
            log.debug(
                "process_pushed_document: IRRELEVANT path=%s consecutive=%d",
                hit.path, consecutive_irrelevant,
            )
            if consecutive_irrelevant >= CONFIG.ingest_irrelevant_stop_n:
                stopped_early = True
                break
        else:
            consecutive_irrelevant = 0
            if result is not None:
                message = f"ingest({source_label}): update {hit.path}"
                sha = wiki_git.commit_file(hit.path, result, message, author=_INGEST_AUTHOR)
                wiki_notify.after_doc_write(hit.path, sha, "edit", _INGEST_AUTHOR)
                committed += 1
                log.info("process_pushed_document: committed %s sha=%s", hit.path, sha)

    log.info(
        "process_pushed_document: done doc_id=%s source_type=%s candidates=%d "
        "llm_calls=%d committed=%d irrelevant=%d stopped_early=%s duration_ms=%d",
        doc_id, source_type, len(hits),
        llm_calls, committed, irrelevant, stopped_early,
        int((time.monotonic() - t_start) * 1000),
    )
