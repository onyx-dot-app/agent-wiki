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
from typing import Any, Literal

from app.config import CONFIG
from app.ingest import eval_sample as ingest_eval_sample
from app.ingest import intent as ingest_intent
from app.ingest import search as ingest_search
from app.ingest import settings as ingest_settings
from app.ingest.source_tiers import is_filtered
from app.ingest.models import WikiUpdateCandidate
from app.llm.agents import ingest_batch_reconciler, ingest_selector, nl_updater
from app.llm.agents.common import IRRELEVANT_SENTINEL
from app.wiki import utils as wiki_utils
from app.llm.agents.tools.errors import ToolError
from app.llm.errors import LLMError
from app.llm.settings import get as get_llm_settings
from app.metrics import (
    ingest_batch_reconciler_duration_seconds,
    ingest_bm25_score_by_outcome,
    ingest_document_chars,
    ingest_document_results_total,
    ingest_llm_calls_per_doc,
    ingest_outcomes_total,
    ingest_queue_depth,
    ingest_requests_total,
    ingest_selector_candidates_filtered,
    ingest_selector_duration_seconds,
)
from app.mcp_server import jobs as mcp_jobs
from app.mcp_server import pubsub as mcp_pubsub
from app.auth import UserMissingError, load_user, set_current_user
from app.tasks import update_frequency
from app.tasks.queues import documents_queue
from app.wiki import agent_activity, constants as wiki_constants, git as wiki_git, update_policy
from app.models.wiki import ChangeKind, CommitMaxRetriesError


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
    #   2. Call app.llm.agents.nl_updater.process_instruction(wiki_path, body, payload, source).
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
      3. Server-side debounce — if a same-(user, path) job committed
         within ``MCP_NL_DEBOUNCE_SECONDS``, succeed with
         ``committed=false reason=debounced``.
      4. Run the document-updater agent.
      5. ``NO_CHANGE`` → succeed with ``committed=false``.
         New body → ``commit_and_fan_out`` → succeed with the sha.
         Drift between enqueue and run is reconciled by the 3-way
         merge there.
         Exception → fail with the error code.
      6. Publish the terminal status to ``job://<id>`` subscribers.
    """
    job = mcp_jobs.get(job_id)
    if job is None:
        log.warning("agent_update_document_nl: job %s not found, dropping", job_id)
        return

    payload = job["payload"]
    rel = payload.get("path")
    instruction = payload.get("instruction") or ""
    agent_name = payload.get("agent_name")

    try:
        user = load_user(job["user_id"])
        agent_token = agent_activity.agent_name_var.set(agent_name)
        try:
            with set_current_user(user):
                mcp_jobs.mark_running(job_id)
                _run_inner(job_id, rel, instruction)
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


def _run_inner(job_id: str, rel: str, instruction: str) -> None:
    """Inside the worker's user context. Splits out so the outer
    function can wrap exceptions uniformly."""
    if not rel:
        mcp_jobs.mark_failed(job_id, error="invalid_path")
        mcp_pubsub.publish_job_update(job_id, "failed")
        return

    if not wiki_utils.file_exists(rel):
        mcp_jobs.mark_failed(job_id, error=f"file not found: {rel}")
        mcp_pubsub.publish_job_update(job_id, "failed")
        return

    # The sub-agent regenerates from current content; drift between enqueue and
    # run is reconciled by the 3-way merge in commit_and_fan_out below
    # (base_body + ai_merge).
    head_sha = wiki_git.head_sha_for_path(rel)

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

    old_body = wiki_utils.read_existing(rel)
    try:
        new_body = nl_updater.process_instruction(
            wiki_path=rel,
            current_body=old_body,
            payload={"instruction": instruction},
            source="update_doc_nl",
        )
    except LLMError as exc:
        mcp_jobs.mark_failed(job_id, error=f"llm_error: {exc}")
        mcp_pubsub.publish_job_update(job_id, "failed")
        return

    if new_body is None:
        mcp_jobs.mark_succeeded(
            job_id,
            result={"committed": False, "reason": "no_change", "sha": head_sha},
        )
        mcp_pubsub.publish_job_update(job_id, "succeeded")
        return

    try:
        result = wiki_utils.commit_and_fan_out(
            path=rel,
            body=new_body,
            message=f"Doc update: {instruction[:_COMMIT_MESSAGE_MAX]}",
            change_kind=ChangeKind.EDIT,
            base_body=old_body,
            ai_merge=True,
        )
    except LLMError as exc:
        mcp_jobs.mark_failed(job_id, error=f"llm_error: {exc}")
        mcp_pubsub.publish_job_update(job_id, "failed")
        return
    except CommitMaxRetriesError as exc:
        mcp_jobs.mark_failed(
            job_id,
            error="max_retries_exceeded",
            result={"retries": exc.retries, "current_sha": exc.current_sha},
        )
        mcp_pubsub.publish_job_update(job_id, "failed")
        return
    except ToolError as exc:
        mcp_jobs.mark_failed(job_id, error=str(exc))
        mcp_pubsub.publish_job_update(job_id, "failed")
        return

    if result is None:
        mcp_jobs.mark_succeeded(
            job_id,
            result={"committed": False, "reason": "no_change", "sha": head_sha},
        )
        mcp_pubsub.publish_job_update(job_id, "succeeded")
        return

    mcp_jobs.mark_succeeded(
        job_id,
        result={
            "committed": True,
            "sha": result.sha,
            "diff": wiki_utils.unified_diff(result.old_body, result.new_body, rel),
            "broken_links": wiki_utils.broken_links(rel, result.new_body),
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
    """Reconcile a connector-pushed document into the wiki (task entry point).

    Ingestion has no human principal — it's authenticated by the shared ingest
    key (see ``app/api/documents.py``), so there's no user to credit. Bind the
    ``Onyx Ingest`` author for the run so its commits surface under that name in
    history instead of the generic fallback, then delegate to the reconciler.
    """
    with wiki_utils.system_author(wiki_constants.INGEST_AUTHOR):
        _reconcile_pushed_document(push)


def _reconcile_pushed_document(push: dict[str, Any]) -> None:
    """Reconcile a document pushed from an external system into the wiki.

    ``push`` is the validated payload from POST /api/wiki/ingest. Shape:
    ``{content, title?, source?, source_document_id?, metadata?,
       updated_at?, diff?}``.

    Pipeline:
      1. Drop filtered sources silently.
      2. BM25 search + title boost + score threshold to find candidates.
      3. Weak-model pre-filter (optional): skip if ingest_selector_model unset
         or same as the main model.
      4. Batch reconcile with the main model — one call decides and produces
         new bodies for all remaining candidates. Skipped when model is unset.
         - new body   → commit + fan-out, reset irrelevant counter
         - NO_CHANGE  → skip commit, reset irrelevant counter
         - IRRELEVANT → increment counter; stop when ≥ INGEST_IRRELEVANT_STOP_N
    """
    source_type = push.get("source")
    title = push.get("title")
    content: str = push.get("content") or ""
    doc_id = push.get("source_document_id") or push.get("title") or "unknown"

    log.info(
        "process_pushed_document source=%s title=%s len=%d",
        source_type,
        title,
        len(content),
    )

    ingest_requests_total.labels(source_type=source_type or "unknown").inc()
    ingest_document_chars.labels(source_type=source_type or "unknown").observe(len(content))
    ingest_queue_depth.set(documents_queue.depth().pending)

    if is_filtered(source_type):
        log.debug("process_pushed_document: filtered source %s, dropping", source_type)
        ingest_outcomes_total.labels(outcome="filtered", wiki_path="").inc()
        return

    metadata: dict[str, Any] = push.get("metadata") or {}
    url: str = str(push.get("url") or metadata.get("url") or "")

    def _record_search_drops(
        dropped: list[ingest_search.SearchHit],
        outcome: Literal["filtered_by_bm25_score", "filtered_by_search_rank"],
    ) -> None:
        # Post-process candidates dropped before the reconciler — by the BM25
        # score threshold (``filtered_by_bm25_score``) or by the top-N candidate
        # cap (``filtered_by_search_rank``): the per-pair outcome metric, and
        # (when eval logging is on) an eval row — reading the page body here
        # since this path never reads it otherwise.
        for hit in dropped:
            ingest_outcomes_total.labels(outcome=outcome, wiki_path=hit.path).inc()
            ingest_bm25_score_by_outcome.labels(outcome=outcome).observe(hit.score)
            if not CONFIG.ingest_eval_logging:
                continue
            try:
                body = wiki_git.read_file(hit.path)
            except Exception:
                log.warning(
                    "ingest_eval_sample: failed to read %s for %s sample",
                    hit.path, outcome,
                    exc_info=True,
                )
                continue
            try:
                ingest_eval_sample.log_sample(
                    source_document_id=push.get("source_document_id"),
                    source_type=source_type,
                    source_title=title,
                    source_url=url if url else None,
                    source_content=content,
                    wiki_path=hit.path,
                    wiki_body_before=body,
                    outcome=outcome,
                    bm25_score=hit.score,
                    commit_sha=None,
                )
            except Exception:
                log.warning(
                    "ingest_eval_sample: failed to log %s sample", outcome,
                    exc_info=True,
                )

    t_start = time.monotonic()
    used_fallback = False
    # Widen the candidate fetch only when eval logging is on, so we can record
    # real search hits beyond the top-N cap (filtered_by_search_rank). The kept
    # set the pipeline acts on is unchanged either way.
    fetch_limit = ingest_search.EVAL_WIDE_FETCH_LIMIT if CONFIG.ingest_eval_logging else None
    try:
        search_result = ingest_search.candidates(content, title, fetch_limit=fetch_limit)
    except ingest_search.IngestSearchError:
        used_fallback = True
        # The candidate search failed — almost always because the full document
        # body exceeds OpenSearch's boolean-clause limit. Only large documents
        # reach here, so retry with a compact query (and pay the LLM cost only
        # when needed): an LLM-distilled update-intent when a cheap model is
        # configured, else a deterministic bounded-terms query.
        selector_model = get_llm_settings().ingest_selector_model
        query = (
            ingest_intent.generate_search_query(title=title, content=content, model=selector_model)
            if selector_model
            else None
        )
        strategy = "llm-intent"
        if not query:
            query = ingest_search.bounded_query(content)
            strategy = "bounded-terms"
        log.info(
            "process_pushed_document: oversized query, retrying via %s, doc_id=%s",
            strategy, doc_id,
        )
        try:
            search_result = ingest_search.candidates(query, title)
        except ingest_search.IngestSearchError:
            log.warning(
                "process_pushed_document: candidate search FAILED after %s fallback "
                "(document dropped), doc_id=%s",
                strategy, doc_id, exc_info=True,
            )
            return

    # Only record search drops from the primary, full-content search. The
    # fallback scores against a lossy summary query, so its below-threshold /
    # below-rank pages aren't comparable to the document and would mislabel the
    # eval rows. rank_dropped is non-empty only when eval logging widened the
    # fetch, so it's a no-op otherwise.
    if not used_fallback:
        _record_search_drops(search_result.dropped, "filtered_by_bm25_score")
        _record_search_drops(search_result.rank_dropped, "filtered_by_search_rank")
    hits = search_result.passed
    if not hits:
        log.info("process_pushed_document: no BM25 candidates above threshold, doc_id=%s", doc_id)
        ingest_outcomes_total.labels(outcome="no_candidates", wiki_path="").inc()
        ingest_document_results_total.labels(result="no_candidates").inc()
        return

    source_label = source_type or "external"

    # Read all candidate bodies upfront — needed by both the selector and the
    # main reconciler loop. Skip unreadable files early so the selector sees the
    # same set the reconciler will act on. Resolve every candidate's update
    # policy in one query: drop pages whose policy disables ingestion
    # auto-update *before* any LLM call, and carry each kept page's resolved
    # update instruction onto its candidate for the reconciler prompt.
    policies = update_policy.resolve_for_paths([hit.path for hit in hits])
    # Admin hard cap: pages that already hit the cap in the trailing 24h are
    # dropped here, before any LLM call, so a runaway page stops burning tokens.
    # Dynamic — no persisted disable; a page resumes on its own once its rolling
    # window falls back under the cap. 0 disables the cap.
    #
    # The count is a per-hit git read, only when cap > 0 — in line with the
    # per-hit read_file below and fine for today's small candidate sets. If
    # candidate sets grow, give ingest_update_times_24h a multi-path sibling and
    # batch the cap reads like resolve_for_paths above.
    cap = ingest_settings.get().auto_update_cap
    readable: list[WikiUpdateCandidate] = []
    for hit in hits:
        policy = policies.get(hit.path)
        if policy is not None and policy.ingestion_auto_update_disabled:
            ingest_outcomes_total.labels(
                outcome="ingestion_auto_update_disabled", wiki_path=hit.path
            ).inc()
            log.debug(
                "process_pushed_document: ingestion auto-update disabled for %s, skipping",
                hit.path,
            )
            continue
        cap_count = len(wiki_git.ingest_update_times_24h(hit.path)) if cap > 0 else 0
        if cap > 0 and cap_count >= cap:
            ingest_outcomes_total.labels(
                outcome="auto_update_cap_exceeded", wiki_path=hit.path
            ).inc()
            log.info(
                "process_pushed_document: %s hit the %d/24h auto-update cap, skipping",
                hit.path,
                cap,
            )
            # Record the (deduped) activity event from here, where we actually
            # block a push — so it fires even for pages already over the cap when
            # an admin set/lowered it (which have no future crossing commit).
            update_frequency.record_auto_update_capped(hit.path, cap_count, cap)
            continue
        try:
            body = wiki_git.read_file(hit.path)
        except Exception:
            log.debug("process_pushed_document: skipping unreadable %s", hit.path)
            continue
        readable.append(
            WikiUpdateCandidate(
                hit=hit,
                body=body,
                update_instruction=policy.update_instruction if policy else None,
            )
        )

    if not readable:
        ingest_outcomes_total.labels(outcome="no_candidates", wiki_path="").inc()
        ingest_document_results_total.labels(result="no_candidates").inc()
        return

    # Stage 3: weak-model pre-filter (skipped when selector_model is unset or
    # matches the main model).
    llm_s = get_llm_settings()
    selector_model = llm_s.ingest_selector_model
    if selector_model and selector_model != llm_s.model:
        before_filter = readable
        t_selector = time.monotonic()
        readable = ingest_selector.select_candidates(
            title=title,
            content=content,
            candidates=before_filter,
            model=selector_model,
        )
        ingest_selector_duration_seconds.observe(time.monotonic() - t_selector)
        dropped = len(before_filter) - len(readable)
        ingest_selector_candidates_filtered.observe(dropped)
        kept_paths = {c.hit.path for c in readable}
        for c in before_filter:
            if c.hit.path not in kept_paths:
                ingest_outcomes_total.labels(
                    outcome="filtered_by_selector", wiki_path=c.hit.path
                ).inc()
                ingest_bm25_score_by_outcome.labels(outcome="filtered_by_selector").observe(c.hit.score)
                log.debug("process_pushed_document: filtered_by_selector path=%s", c.hit.path)
                if CONFIG.ingest_eval_logging:
                    try:
                        ingest_eval_sample.log_sample(
                            source_document_id=push.get("source_document_id"),
                            source_type=source_type,
                            source_title=title,
                            source_url=url if url else None,
                            source_content=content,
                            wiki_path=c.hit.path,
                            wiki_body_before=c.body,
                            outcome="filtered_by_selector",
                            bm25_score=c.hit.score,
                            commit_sha=None,
                        )
                    except Exception:
                        log.warning(
                            "ingest_eval_sample: failed to log filtered_by_selector sample",
                            exc_info=True,
                        )

    # Stage 4: batch reconcile with the main model — one call decides and
    # produces new bodies for all candidates. Skipped when model is unset.
    if llm_s.model:
        t_batch = time.monotonic()
        batch_results, llm_calls = ingest_batch_reconciler.batch_reconcile(
            title=title,
            url=url,
            content=content,
            source=source_label,
            candidates=readable,
            model=llm_s.model,
            metadata=metadata,
        )
        ingest_batch_reconciler_duration_seconds.observe(time.monotonic() - t_batch)
    else:
        batch_results = [IRRELEVANT_SENTINEL] * len(readable)
        llm_calls = 0

    consecutive_irrelevant = 0
    irrelevant = 0
    committed = 0
    no_change = 0
    stopped_early = False

    for c, result in zip(readable, batch_results):
        if result == IRRELEVANT_SENTINEL:
            irrelevant += 1
            consecutive_irrelevant += 1
            ingest_outcomes_total.labels(outcome="irrelevant", wiki_path=c.hit.path).inc()
            ingest_bm25_score_by_outcome.labels(outcome="irrelevant").observe(c.hit.score)
            log.debug(
                "process_pushed_document: IRRELEVANT path=%s consecutive=%d",
                c.hit.path,
                consecutive_irrelevant,
            )
            if CONFIG.ingest_eval_logging:
                try:
                    ingest_eval_sample.log_sample(
                        source_document_id=push.get("source_document_id"),
                        source_type=source_type,
                        source_title=title,
                        source_url=url if url else None,
                        source_content=content,
                        wiki_path=c.hit.path,
                        wiki_body_before=c.body,
                        outcome="irrelevant",
                        bm25_score=c.hit.score,
                        commit_sha=None,
                    )
                except Exception:
                    log.warning("ingest_eval_sample: failed to log irrelevant sample", exc_info=True)
            if consecutive_irrelevant >= CONFIG.ingest_irrelevant_stop_n:
                stopped_early = True
                break
        elif result is not None:
            consecutive_irrelevant = 0
            message = f"ingest({source_label}): update {c.hit.path}"
            meta_lines: list[str] = []
            if title:
                meta_lines.append(f"Title: {title}")
            if url:
                meta_lines.append(f"Source: {url}")
            if meta_lines:
                message += "\n\n" + "\n".join(meta_lines)
            try:
                commit_result = wiki_utils.commit_and_fan_out(
                    path=c.hit.path,
                    body=result,
                    message=message,
                    change_kind=ChangeKind.EDIT,
                    base_body=c.body,
                    ai_merge=True,
                    skip_acl=True,
                )
            except CommitMaxRetriesError:
                log.warning("process_pushed_document: max retries for %s, skipping", c.hit.path)
                continue
            except LLMError as exc:
                # ai_merge fallback failed — skip this candidate, don't abort the batch.
                log.warning("process_pushed_document: merge LLM error for %s: %s", c.hit.path, exc)
                continue
            if commit_result is None:
                # Concurrent edit produced identical content — treat as no_change.
                no_change += 1
                ingest_outcomes_total.labels(outcome="no_change", wiki_path=c.hit.path).inc()
                ingest_bm25_score_by_outcome.labels(outcome="no_change").observe(c.hit.score)
                continue
            sha = commit_result.sha
            committed += 1
            ingest_outcomes_total.labels(outcome="committed", wiki_path=c.hit.path).inc()
            ingest_bm25_score_by_outcome.labels(outcome="committed").observe(c.hit.score)
            log.info("process_pushed_document: committed %s sha=%s", c.hit.path, sha)
            if CONFIG.ingest_eval_logging:
                try:
                    ingest_eval_sample.log_sample(
                        source_document_id=push.get("source_document_id"),
                        source_type=source_type,
                        source_title=title,
                        source_url=url if url else None,
                        source_content=content,
                        wiki_path=c.hit.path,
                        wiki_body_before=c.body,
                        outcome="committed",
                        bm25_score=c.hit.score,
                        commit_sha=sha,
                    )
                except Exception:
                    log.warning("ingest_eval_sample: failed to log committed sample", exc_info=True)
        else:
            consecutive_irrelevant = 0
            no_change += 1
            ingest_outcomes_total.labels(outcome="no_change", wiki_path=c.hit.path).inc()
            ingest_bm25_score_by_outcome.labels(outcome="no_change").observe(c.hit.score)
            if CONFIG.ingest_eval_logging:
                try:
                    ingest_eval_sample.log_sample(
                        source_document_id=push.get("source_document_id"),
                        source_type=source_type,
                        source_title=title,
                        source_url=url if url else None,
                        source_content=content,
                        wiki_path=c.hit.path,
                        wiki_body_before=c.body,
                        outcome="no_change",
                        bm25_score=c.hit.score,
                        commit_sha=None,
                    )
                except Exception:
                    log.warning("ingest_eval_sample: failed to log no_change sample", exc_info=True)

    ingest_llm_calls_per_doc.observe(llm_calls)
    # Per-document terminal outcome, priority committed > no_change > irrelevant.
    # The catch-all is irrelevant (e.g. all candidates were skipped on error).
    doc_result = "committed" if committed else "no_change" if no_change else "irrelevant"
    ingest_document_results_total.labels(result=doc_result).inc()
    log.info(
        "process_pushed_document: done doc_id=%s source=%s candidates=%d "
        "llm_calls=%d committed=%d irrelevant=%d stopped_early=%s duration_ms=%d",
        doc_id,
        source_type,
        len(hits),
        llm_calls,
        committed,
        irrelevant,
        stopped_early,
        int((time.monotonic() - t_start) * 1000),
    )
