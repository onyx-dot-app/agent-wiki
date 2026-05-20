"""Batch decision phase for the ingest pipeline.

One strong-model call classifies all post-selector candidates as
IRRELEVANT / NO_CHANGE / NEEDS_UPDATE. Only NEEDS_UPDATE candidates
proceed to the per-page reconciler, avoiding LLM calls on the ~87% of
candidates the reconciler would otherwise mark IRRELEVANT.

Fails open — any error marks all candidates NEEDS_UPDATE so the
reconciler always has something to work with.
"""
from __future__ import annotations

import json
import logging
from enum import StrEnum
from typing import Any, cast

from app.ingest.models import WikiUpdateCandidate
from app.llm import client
from app.llm.prompts import load_prompt
from app.tracing import trace_flow

log = logging.getLogger(__name__)

# Same 200k budget as the selector. Incoming content is truncated to a
# larger slice than the selector because the strong model needs more
# context to make accurate NO_CHANGE decisions.
_CLASSIFIER_BUDGET_CHARS = 200_000
_CLASSIFIER_CONTENT_CHARS = 40_000


class Verdict(StrEnum):
    IRRELEVANT = "IRRELEVANT"
    NO_CHANGE = "NO_CHANGE"
    NEEDS_UPDATE = "NEEDS_UPDATE"


def classify_candidates(
    *,
    title: str | None,
    url: str,
    content: str,
    source: str,
    candidates: list[WikiUpdateCandidate],
    model: str,
) -> list[Verdict]:
    """Classify each candidate in one or more LLM calls.

    Returns verdicts parallel to ``candidates``. Falls back to
    NEEDS_UPDATE for all on any LLM or parse error.
    """
    if not candidates:
        return []

    content_truncated = content[:_CLASSIFIER_CONTENT_CHARS]
    candidate_budget = max(_CLASSIFIER_BUDGET_CHARS - len(content_truncated), 0)
    batches = _batch_by_chars(candidates, candidate_budget)

    verdicts: list[Verdict] = []
    for batch in batches:
        verdicts.extend(
            _classify_batch(
                title=title,
                url=url,
                content=content_truncated,
                source=source,
                batch=batch,
                model=model,
            )
        )

    log.info(
        "ingest_batch_classifier: needs_update=%d no_change=%d irrelevant=%d total=%d batches=%d",
        sum(1 for v in verdicts if v == Verdict.NEEDS_UPDATE),
        sum(1 for v in verdicts if v == Verdict.NO_CHANGE),
        sum(1 for v in verdicts if v == Verdict.IRRELEVANT),
        len(verdicts),
        len(batches),
    )
    return verdicts


def _batch_by_chars(
    candidates: list[WikiUpdateCandidate],
    budget: int,
) -> list[list[WikiUpdateCandidate]]:
    """Single batch when all candidates fit; otherwise split greedily."""
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


def _classify_batch(
    *,
    title: str | None,
    url: str,
    content: str,
    source: str,
    batch: list[WikiUpdateCandidate],
    model: str,
) -> list[Verdict]:
    candidate_text = "\n\n".join(
        f"[{i + 1}] {c.hit.path}\n{c.body}" for i, c in enumerate(batch)
    )

    system = load_prompt("ingest_batch_classifier.system")
    user = load_prompt("ingest_batch_classifier.input").format(
        title=title or "(no title)",
        url=url or "",
        source=source,
        content=content,
        candidates=candidate_text,
    )

    try:
        with trace_flow("agent.ingest_batch_classifier"):
            result = client.complete(
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                model=model,
            )
        text = result.text.strip()
        raw: Any = json.loads(text)
        if not isinstance(raw, list) or len(raw) != len(batch):
            raise ValueError(f"expected list of {len(batch)} verdicts, got {raw!r}")
        valid = {v.value for v in Verdict}
        return [
            Verdict(v) if isinstance(v, str) and v in valid else Verdict.NEEDS_UPDATE
            for v in cast(list[object], raw)
        ]
    except Exception:
        log.warning(
            "ingest_batch_classifier: batch failed, marking all NEEDS_UPDATE", exc_info=True
        )
        return [Verdict.NEEDS_UPDATE] * len(batch)
