"""Distill an incoming document into a compact BM25 query.

The candidate search normally uses the raw document body as the query. When a
document is large its body exceeds OpenSearch's boolean-clause limit and the
search is rejected (see ``app.ingest.search.candidates``). For those documents
a cheap LLM extracts the update-intent — summary, candidate updates, and named
entities — which is short enough to query with and keeps the discriminating
tokens. Fails open: returns None on any model or parse error so the caller can
fall back to a deterministic bounded-terms query.
"""
from __future__ import annotations

import json
import logging
from typing import Any, cast

from app.llm import client
from app.llm.prompts import load_prompt
from app.tracing import trace_flow

log = logging.getLogger(__name__)

# Cap the content sent to the model — enough to capture the document's subject
# without paying full-document token cost on this cheap call.
_INTENT_CONTENT_CHARS = 20_000


def generate_search_query(*, title: str | None, content: str, model: str) -> str | None:
    """Return a compact query (summary + candidate updates + entities) distilled
    from the document, or None if the model is unavailable or the response can't
    be parsed."""
    system = load_prompt("ingest_intent.system")
    user = load_prompt("ingest_intent.input").format(
        title=title or "(no title)",
        content=content[:_INTENT_CONTENT_CHARS],
    )
    try:
        with trace_flow("agent.ingest_intent"):
            result = client.complete(
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                model=model,
            )
        text = result.text.strip()
        # Some models wrap the JSON in a ```/```json code fence; strip it.
        if text.startswith("```"):
            text = text.split("```", 2)[1].removeprefix("json").removeprefix("JSON").strip()
        raw: Any = json.loads(text)
        if not isinstance(raw, dict):
            raise ValueError(f"expected object, got {type(raw).__name__}")
        data = cast(dict[str, Any], raw)
        parts: list[str] = []
        summary = data.get("summary")
        if isinstance(summary, str):
            parts.append(summary)
        for key in ("candidate_updates", "entities"):
            vals = data.get(key)
            if isinstance(vals, list):
                parts.extend(v for v in cast(list[object], vals) if isinstance(v, str))
    except Exception:
        log.warning("ingest_intent: failed to generate search query", exc_info=True)
        return None

    query = " ".join(p.strip() for p in parts if p.strip())
    return query or None
