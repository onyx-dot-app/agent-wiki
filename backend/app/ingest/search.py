"""BM25 candidate search for document ingestion.

Wraps ``app.db.fts.search`` with:
  - a minimum score threshold to drop obvious non-matches
  - a title similarity boost so topically-named pages rank higher
  - descending score ordering (most relevant first)

Parameters are read from env vars so they can be tuned without a deploy:
  INGEST_BM25_MIN_SCORE    float, default 5.0
  INGEST_BM25_TITLE_BOOST  float, default 2.0
  INGEST_BM25_LIMIT        int,   default 20  (top-N candidates kept for the pipeline)
"""
from __future__ import annotations

import re

import logging
from collections import Counter

from pydantic import BaseModel

from app.config import CONFIG
from app.db.fts import SearchHit, search as fts_search
from app.metrics import ingest_bm25_hits, ingest_bm25_passed, ingest_bm25_score

log = logging.getLogger(__name__)

_TOKEN_RE = re.compile(r"\w+")

# Wide candidate fetch used only when eval logging is on: pull far more than the
# top-N kept for the pipeline so we can record real search hits that fell outside
# the cap (``filtered_by_search_rank``). A page ranked below this is noise. Not a
# tuned knob — INGEST_BM25_LIMIT stays the production selection size.
EVAL_WIDE_FETCH_LIMIT = 500


class IngestSearchError(Exception):
    """Raised when the BM25 candidate search fails at the backend (e.g.
    OpenSearch rejecting an oversized query), as distinct from returning no
    matches. The reconciler logs it and drops the document."""


def bounded_query(content: str, max_terms: int = 200) -> str:
    """Reduce ``content`` to its most frequent terms, capped at ``max_terms``.

    A deterministic fallback query that stays well under OpenSearch's boolean
    clause limit, used when the LLM intent path is unavailable for a document
    too large to query with its raw body.
    """
    counts = Counter(t for t in _TOKEN_RE.findall(content.lower()) if len(t) > 2)
    return " ".join(term for term, _ in counts.most_common(max_terms))


def _tokens(text: str) -> set[str]:
    return {t.lower() for t in _TOKEN_RE.findall(text)}


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


class CandidateSearch(BaseModel):
    """Result of a BM25 candidate search, all with title-boosted scores:

      - ``passed``       — within the top-N cap and above the score threshold
                           (most relevant first); the set the pipeline acts on.
      - ``dropped``      — within the top-N cap but below the score threshold.
      - ``rank_dropped`` — real search hits that fell *outside* the top-N cap.
                           Only populated when ``fetch_limit`` widens the fetch
                           (eval logging); empty otherwise.

    Pure data — the caller decides what to do with the drops (outcome metric,
    eval logging) via a post-process step, so this module stays free of
    git/DB/metadata concerns."""

    passed: list[SearchHit]
    dropped: list[SearchHit]
    rank_dropped: list[SearchHit] = []


def candidates(
    content: str, title: str | None, *, fetch_limit: int | None = None
) -> CandidateSearch:
    """Return the BM25 candidates split by the top-N cap and score threshold.

    The top ``INGEST_BM25_LIMIT`` hits by raw BM25 are kept for the pipeline and
    split into `passed`/`dropped` by the score threshold — production behavior is
    independent of ``fetch_limit``. When ``fetch_limit`` exceeds that cap, the
    extra hits beyond it are returned as `rank_dropped` so the caller can record
    search hits the cap would otherwise hide (``filtered_by_search_rank``).

    Title similarity boosts the raw BM25 score before thresholding so pages whose
    titles closely match the incoming document rank higher. Emits only search
    telemetry (hits/passed/raw-score) for the kept set; per-drop outcome
    accounting is the caller's post-process (see ``wiki_update``).
    """
    top_n = CONFIG.ingest_bm25_limit
    fetch = max(fetch_limit or top_n, top_n)
    try:
        hits = fts_search(
            content,
            limit=fetch,
            apply_visibility=False,
            raise_on_error=True,
        )
    except Exception as exc:
        # Surface a backend failure (e.g. OpenSearch rejecting an oversized
        # query) as IngestSearchError, distinct from an empty result, so the
        # caller treats it as an error rather than a genuine no-match.
        raise IngestSearchError(str(exc)) from exc
    if not hits:
        log.debug("ingest candidates: no BM25 hits for title=%r", title)
        return CandidateSearch(passed=[], dropped=[])

    query_title_tokens: set[str] = _tokens(title) if title else set()

    def _boost(hit: SearchHit) -> SearchHit:
        score = hit.score
        if query_title_tokens and hit.title:
            sim = _jaccard(query_title_tokens, _tokens(hit.title))
            score += sim * CONFIG.ingest_bm25_title_boost
        return hit.model_copy(update={"score": score})

    # Top-N by raw BM25 (OpenSearch order) is the pipeline set; the rest is the
    # rank-dropped tail. Slicing before boosting keeps the kept set identical to
    # the non-widened fetch.
    in_scope = hits[:top_n]
    tail = hits[top_n:]

    boosted: list[SearchHit] = []
    dropped: list[SearchHit] = []
    for hit in in_scope:
        scored = _boost(hit)
        ingest_bm25_score.observe(hit.score)
        log.debug(
            "ingest candidate: path=%r raw_bm25=%.3f boosted=%.3f threshold=%.3f pass=%s",
            hit.path, hit.score, scored.score, CONFIG.ingest_bm25_min_score,
            scored.score >= CONFIG.ingest_bm25_min_score,
        )
        if scored.score >= CONFIG.ingest_bm25_min_score:
            boosted.append(scored)
        else:
            dropped.append(scored)

    rank_dropped = [_boost(hit) for hit in tail]

    ingest_bm25_hits.observe(len(in_scope))
    ingest_bm25_passed.observe(len(boosted))
    log.info(
        "ingest candidates: title=%r hits=%d passed=%d rank_dropped=%d threshold=%.3f",
        title, len(in_scope), len(boosted), len(rank_dropped), CONFIG.ingest_bm25_min_score,
    )
    boosted.sort(key=lambda h: h.score, reverse=True)
    rank_dropped.sort(key=lambda h: h.score, reverse=True)
    return CandidateSearch(passed=boosted, dropped=dropped, rank_dropped=rank_dropped)
