"""The relevance filter as a service.

Given a document and its candidate pages, enrich their embeddings, run the
configured :class:`RelevanceFilter`, and return which pages were kept vs
dropped. Reconcile-agnostic: it takes the pipeline carriers
(:class:`IngestionDocument`, :class:`CandidatePage`) and returns a plain
:class:`RelevanceResult`, so the *caller* decides what to do with it — observe
in shadow, or actually drop in enforce mode. This keeps the filtering logic in
one place instead of inlined in the reconcile task.

Fail-open: any failure (or empty input) keeps every page, matching the filter
contract — filtering never removes a candidate the pipeline would otherwise
have considered.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from app.ingest import enrich
from app.ingest.relevance.factory import build_relevance_filter
from app.ingest.relevance.filter import RelevanceFilter
from app.ingest.types import CandidatePage, IngestionDocument

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class RelevanceResult:
    """The input pages partitioned by the filter's verdict.

    ``kept`` + ``dropped`` together are exactly the input pages (the caller's
    own objects). ``kept`` is ordered most-relevant-first when the filter
    exposes scores (unscored pages last, in input order); ``dropped`` keeps
    input order. ``scores`` maps page path -> the filter's numeric relevance
    for every pair it scored — for candidate ordering, telemetry, and
    threshold calibration. Empty when the filter has no numeric score.
    """

    kept: list[CandidatePage]
    dropped: list[CandidatePage]
    scores: dict[str, float] = field(default_factory=dict)


class RelevanceService:
    """Runs a :class:`RelevanceFilter` over a document's candidate pages.

    Holds the filter (built once, e.g. the two-tower model loaded on
    construction) so ``evaluate`` doesn't rebuild it per call.
    """

    def __init__(self, relevance_filter: RelevanceFilter) -> None:
        self._filter = relevance_filter

    def evaluate(
        self, doc: IngestionDocument, pages: list[CandidatePage]
    ) -> RelevanceResult:
        """Enrich embeddings, score, filter, and partition ``pages``.

        Fail-open: on any error every page is kept (unordered, no scores), so a
        filter hiccup never drops a candidate. Returns the caller's own page
        objects; kept most-relevant-first when scores exist.
        """
        if not pages:
            return RelevanceResult(kept=[], dropped=[])
        try:
            enriched_doc = enrich.with_document_embedding(doc)
            enriched_pages = enrich.with_page_embeddings(list(pages))
            raw_scores = self._filter.score_pages(enriched_doc, enriched_pages)
            kept_paths = {
                p.path for p in self._filter.keep_relevant(enriched_doc, enriched_pages)
            }
        except Exception:
            log.warning(
                "relevance service: evaluate failed; keeping all candidates", exc_info=True
            )
            return RelevanceResult(kept=list(pages), dropped=[])
        scores = (
            {}
            if raw_scores is None
            else {
                p.path: s for p, s in zip(pages, raw_scores, strict=True) if s is not None
            }
        )
        kept = [p for p in pages if p.path in kept_paths]
        # Most-relevant-first; stable sort keeps unscored (fail-open) pages in
        # input order after every scored page.
        kept.sort(key=lambda p: scores.get(p.path, float("-inf")), reverse=True)
        dropped = [p for p in pages if p.path not in kept_paths]
        return RelevanceResult(kept=kept, dropped=dropped, scores=scores)


_service: RelevanceService | None = None


def get_relevance_service() -> RelevanceService:
    """The process-wide service, built lazily on first use so the underlying
    filter (e.g. the two-tower ONNX model) is loaded once, not per document."""
    global _service
    if _service is None:
        _service = RelevanceService(build_relevance_filter())
    return _service
