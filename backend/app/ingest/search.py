"""BM25 candidate search for document ingestion.

Wraps ``app.db.fts.search`` with:
  - a minimum score threshold to drop obvious non-matches
  - a title similarity boost so topically-named pages rank higher
  - descending score ordering (most relevant first)

Parameters are read from env vars so they can be tuned without a deploy:
  INGEST_BM25_MIN_SCORE    float, default 1.0
  INGEST_BM25_TITLE_BOOST  float, default 2.0
  INGEST_BM25_LIMIT        int,   default 20  (candidates fetched from OS)
"""
from __future__ import annotations

import re

from app.config import CONFIG
from app.db.fts import SearchHit, search as fts_search

_TOKEN_RE = re.compile(r"\w+")


def _tokens(text: str) -> set[str]:
    return {t.lower() for t in _TOKEN_RE.findall(text)}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def candidates(content: str, title: str | None) -> list[SearchHit]:
    """Return BM25 candidates above the score threshold, most relevant first.

    Title similarity boosts the raw BM25 score before thresholding so
    pages whose titles closely match the incoming document rank higher.
    """
    hits = fts_search(content, limit=CONFIG.ingest_bm25_limit, apply_visibility=False)
    if not hits:
        return []

    query_title_tokens: set[str] = _tokens(title) if title else set()

    boosted: list[SearchHit] = []
    for hit in hits:
        score = hit.score
        if query_title_tokens and hit.title:
            sim = _jaccard(query_title_tokens, _tokens(hit.title))
            score += sim * CONFIG.ingest_bm25_title_boost
        if score >= CONFIG.ingest_bm25_min_score:
            boosted.append(hit.model_copy(update={"score": score}))

    boosted.sort(key=lambda h: h.score, reverse=True)
    return boosted
