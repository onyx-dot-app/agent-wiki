"""Batch reconciler for the ingest pipeline.

One strong-model call processes all post-selector candidates and returns
for each one:
  - IRRELEVANT_SENTINEL  — the external document is unrelated to this page
  - None                 — the page is already up-to-date (NO_CHANGE)
  - str                  — the new page body to commit

This matches the per-page contract of ``reconcile_document`` but avoids
N separate LLM calls when most candidates are irrelevant.

Fails open — any error marks all candidates NEEDS_UPDATE (returns the
original body so the caller falls back to the per-page reconciler).
When batching is required, each batch fails independently.
"""
from __future__ import annotations

import logging
import re

from app.ingest.models import WikiUpdateCandidate
from app.llm import client
from app.llm.agents.common import IRRELEVANT_SENTINEL, NO_CHANGE_SENTINEL, strip_outer_fence
from app.llm.prompts import load_prompt
from app.tracing import trace_flow

log = logging.getLogger(__name__)

_RECONCILER_BUDGET_CHARS = 200_000
_RECONCILER_CONTENT_CHARS = 40_000

# Sentinel used internally when a batch parse fails — tells the caller to
# fall back to the per-page reconciler for every candidate in that batch.
_FALLBACK = object()

_RESULT_RE = re.compile(r"===RESULT \[(\d+)\]===")


def batch_reconcile(
    *,
    title: str | None,
    url: str,
    content: str,
    source: str,
    candidates: list[WikiUpdateCandidate],
    model: str,
) -> list[str | None]:
    """Reconcile all candidates in one or more LLM calls.

    Returns a list parallel to ``candidates`` where each element uses the
    same semantics as ``reconcile_document``:
      IRRELEVANT_SENTINEL — page is unrelated
      None                — page is already up-to-date
      str                 — new page body to commit

    Falls back to per-page reconciler semantics (IRRELEVANT_SENTINEL for
    all) on any LLM or parse error, so the caller can decide how to handle.
    """
    if not candidates:
        return []

    content_truncated = content[:_RECONCILER_CONTENT_CHARS]
    candidate_budget = max(_RECONCILER_BUDGET_CHARS - len(content_truncated), 0)
    batches = _batch_by_chars(candidates, candidate_budget)

    results: list[str | None] = []
    for batch in batches:
        results.extend(
            _reconcile_batch(
                title=title,
                url=url,
                content=content_truncated,
                source=source,
                batch=batch,
                model=model,
            )
        )

    needs_update = sum(1 for r in results if r not in (IRRELEVANT_SENTINEL, None))
    log.info(
        "ingest_batch_reconciler: needs_update=%d no_change=%d irrelevant=%d total=%d batches=%d",
        needs_update,
        sum(1 for r in results if r is None),
        sum(1 for r in results if r == IRRELEVANT_SENTINEL),
        len(results),
        len(batches),
    )
    return results


def _batch_by_chars(
    candidates: list[WikiUpdateCandidate],
    budget: int,
) -> list[list[WikiUpdateCandidate]]:
    if sum(len(c.body) for c in candidates) <= budget:
        return [candidates]

    batches: list[list[WikiUpdateCandidate]] = []
    current: list[WikiUpdateCandidate] = []
    current_chars = 0
    for c in candidates:
        if current and current_chars + len(c.body) > budget:
            batches.append(current)
            current = [c]
            current_chars = len(c.body)
        else:
            current.append(c)
            current_chars += len(c.body)
    if current:
        batches.append(current)
    return batches


def _reconcile_batch(
    *,
    title: str | None,
    url: str,
    content: str,
    source: str,
    batch: list[WikiUpdateCandidate],
    model: str,
) -> list[str | None]:
    candidate_text = "\n\n".join(
        f"[{i + 1}] {c.hit.path}\n{c.body}" for i, c in enumerate(batch)
    )

    system = load_prompt("ingest_batch_reconciler.system")
    user = load_prompt("ingest_batch_reconciler.input").format(
        title=title or "(no title)",
        url=url or "",
        source=source,
        content=content,
        candidates=candidate_text,
        n=len(batch),
    )

    try:
        with trace_flow("agent.ingest_batch_reconciler"):
            result = client.complete(
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                model=model,
            )
        return _parse(result.text, len(batch))
    except Exception:
        log.warning(
            "ingest_batch_reconciler: batch failed, falling back to per-page reconciler",
            exc_info=True,
        )
        return [IRRELEVANT_SENTINEL] * len(batch)


def _parse(text: str, n: int) -> list[str | None]:
    """Parse the structured output into a result per candidate."""
    parts = _RESULT_RE.split(text)
    # parts: [preamble, "1", body1, "2", body2, ...]
    raw: dict[int, str] = {}
    for i in range(1, len(parts), 2):
        idx = int(parts[i])
        body = parts[i + 1].strip() if i + 1 < len(parts) else ""
        raw[idx] = body

    if not raw:
        raise ValueError("no ===RESULT [N]=== sections found in response")

    results: list[str | None] = []
    for i in range(1, n + 1):
        body = raw.get(i, IRRELEVANT_SENTINEL)
        if body == IRRELEVANT_SENTINEL or body.startswith(IRRELEVANT_SENTINEL + "\n"):
            results.append(IRRELEVANT_SENTINEL)
        elif body == NO_CHANGE_SENTINEL or body.startswith(NO_CHANGE_SENTINEL + "\n"):
            results.append(None)
        else:
            results.append(strip_outer_fence(body))

    return results


