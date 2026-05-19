"""Weak-model pre-filter for the ingest pipeline.

Screens BM25 candidates with a single cheap LLM call (or batched calls when
there are many candidates) before handing the survivors to the full reconciler.
Fails open — on any error the original candidate list is returned unchanged.
"""

from __future__ import annotations

import json
import logging

from app.db.fts import SearchHit
from app.llm import client
from app.llm.prompts import load_prompt
from app.tracing import trace_flow

log = logging.getLogger(__name__)

# Split into multiple calls when candidate count exceeds this.
_SELECTOR_BATCH_SIZE = 20


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

    selected: list[tuple[SearchHit, str]] = []
    for i in range(0, len(candidates), _SELECTOR_BATCH_SIZE):
        batch = candidates[i : i + _SELECTOR_BATCH_SIZE]
        selected.extend(_select_batch(title=title, content=content, batch=batch, model=model))

    log.info(
        "ingest_selector: kept %d/%d candidates model=%s",
        len(selected),
        len(candidates),
        model,
    )
    return selected


def _select_batch(
    *,
    title: str | None,
    content: str,
    batch: list[tuple[SearchHit, str]],
    model: str,
) -> list[tuple[SearchHit, str]]:
    candidate_text = "\n\n".join(
        f"[{i + 1}] {hit.path}\n{body}" for i, (hit, body) in enumerate(batch)
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
        valid = {i for i in kept_indices if isinstance(i, int) and 1 <= i <= len(batch)}
        return [batch[i - 1] for i in sorted(valid)]
    except Exception:
        log.warning("ingest_selector: batch failed, passing batch through", exc_info=True)
        return batch
