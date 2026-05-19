"""Weak-model pre-filter for the ingest pipeline.

Makes a single cheap LLM call with all BM25 candidates and returns the
subset worth sending to the full reconciler. Fails open — on any error
the original candidate list is returned unchanged.
"""

from __future__ import annotations

import json
import logging

from app.db.fts import SearchHit
from app.llm import client
from app.llm.prompts import load_prompt
from app.tracing import trace_flow

log = logging.getLogger(__name__)

# Truncate each candidate body to keep the selector call cheap.
_BODY_PREVIEW_CHARS = 1500


def select_candidates(
    *,
    title: str | None,
    content: str,
    candidates: list[tuple[SearchHit, str]],
    model: str,
) -> list[tuple[SearchHit, str]]:
    """Filter BM25 candidates with a cheap model.

    Returns the subset of candidates the selector considers relevant.
    Falls back to the full list on any LLM or parse error so the main
    reconciler always has something to work with.
    """
    if not candidates:
        return candidates

    candidate_text = "\n\n".join(
        f"[{i + 1}] {hit.path}\n{body[:_BODY_PREVIEW_CHARS]}"
        for i, (hit, body) in enumerate(candidates)
    )

    system = load_prompt("ingest_selector.system")
    user = load_prompt("ingest_selector.input").format(
        title=title or "(no title)",
        content=content,
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
        kept_indices: list[int] = json.loads(text)
        if not isinstance(kept_indices, list):
            raise ValueError(f"expected list, got {type(kept_indices)}")
        valid = {i for i in kept_indices if isinstance(i, int) and 1 <= i <= len(candidates)}
        selected = [candidates[i - 1] for i in sorted(valid)]
        log.info(
            "ingest_selector: kept %d/%d candidates model=%s",
            len(selected),
            len(candidates),
            model,
        )
        return selected
    except Exception:
        log.warning("ingest_selector: failed, passing all candidates through", exc_info=True)
        return candidates
