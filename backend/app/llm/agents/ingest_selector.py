"""Weak-model pre-filter for the ingest pipeline.

Screens BM25 candidates with a single cheap LLM call before handing survivors
to the full reconciler. When the total wiki content exceeds the character
budget, candidates are split into multiple batched calls and results merged.
Fails open — on any error the original candidate list is returned unchanged.
"""

from __future__ import annotations

import json
import logging
from typing import Any, cast

from app.ingest.models import WikiUpdateCandidate
from app.llm import client
from app.llm.agents.common import batch_by_chars
from app.llm.prompts import load_prompt
from app.metrics import (
    ingest_selector_cached_input_tokens,
    ingest_selector_calls_per_doc,
    ingest_selector_input_tokens,
    ingest_selector_output_tokens,
    ingest_selector_uncached_input_tokens,
)
from app.tracing import trace_flow

log = logging.getLogger(__name__)

# Total character budget per selector call. Incoming document content is
# subtracted first; the remainder is available for wiki candidate bodies.
_SELECTOR_BUDGET_CHARS = 200_000

# Incoming document is truncated to this length for the selector — enough
# context to judge relevance without letting a large payload make the cheap
# call as expensive as the full reconciler.
_SELECTOR_CONTENT_CHARS = 20_000


def select_candidates(
    *,
    title: str | None,
    content: str,
    candidates: list[WikiUpdateCandidate],
    model: str,
) -> list[WikiUpdateCandidate]:
    """Filter BM25 candidates with a cheap model.

    Returns the subset of candidates the selector considers relevant.
    Falls back to the full list on any LLM or parse error so the main
    reconciler always has something to work with.
    """
    if not candidates:
        return candidates

    candidate_budget = max(_SELECTOR_BUDGET_CHARS - min(len(content), _SELECTOR_CONTENT_CHARS), 0)
    batches = batch_by_chars(candidates, candidate_budget)

    selected: list[WikiUpdateCandidate] = []
    for batch in batches:
        selected.extend(_select_batch(title=title, content=content, batch=batch, model=model))

    ingest_selector_calls_per_doc.observe(len(batches))
    log.info(
        "ingest_selector: kept %d/%d candidates batches=%d model=%s",
        len(selected),
        len(candidates),
        len(batches),
        model,
    )
    return selected



def _select_batch(
    *,
    title: str | None,
    content: str,
    batch: list[WikiUpdateCandidate],
    model: str,
) -> list[WikiUpdateCandidate]:
    candidate_text = "\n\n".join(
        f"[{i + 1}] {c.hit.path}\n{c.body}" for i, c in enumerate(batch)
    )

    system = load_prompt("ingest_selector.system")
    user = load_prompt("ingest_selector.input").format(
        title=title or "(no title)",
        content=content[:_SELECTOR_CONTENT_CHARS],
        candidates=candidate_text,
    )

    try:
        with trace_flow("agent.ingest_selector"):
            result = client.complete(
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                model=model,
            )
        text = result.text.strip()
        raw: Any = json.loads(text)
        if not isinstance(raw, list):
            raise ValueError(f"expected list, got {type(raw)}")
        valid = {i for i in cast(list[object], raw) if isinstance(i, int) and 1 <= i <= len(batch)}
        kept = [batch[i - 1] for i in sorted(valid)]
    except Exception:
        log.warning("ingest_selector: batch failed, passing batch through", exc_info=True)
        return batch
    try:
        ingest_selector_input_tokens.observe(result.usage.input_tokens)
        ingest_selector_cached_input_tokens.observe(result.usage.cached_input_tokens)
        ingest_selector_uncached_input_tokens.observe(result.usage.uncached_input_tokens)
        ingest_selector_output_tokens.observe(result.usage.output_tokens)
    except Exception:
        log.warning("ingest_selector: could not observe token usage", exc_info=True)
    return kept
