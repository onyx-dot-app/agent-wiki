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

from app.db import page_embeddings
from app.ingest import enrich
from app.ingest.relevance.factory import build_relevance_filter
from app.ingest.relevance.filter import RelevanceFilter
from app.ingest.types import CandidatePage, IngestionDocument
from app.llm import embeddings
from app.metrics import (
    ingest_relevance_candidates,
    ingest_relevance_kept_pages,
    ingest_relevance_scores,
)

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

    def relevant_pages(self, doc: IngestionDocument) -> RelevanceResult | None:
        """Which wiki pages is ``doc`` relevant to, over the whole embedding
        store: embed the document, score every stored page vector (one bulk
        load), and return the partition.

        Returns ``None`` when the document can't be embedded — there is nothing
        to score against, and treating that as keep-all would flood the caller
        with every page. Callers should skip the document (a transient embed
        error self-corrects on its next push).

        Pages carry no body (``body=""``) — relevance needs only vectors;
        callers read bodies for the pages they act on.
        """
        enriched_doc = enrich.with_document_embedding(doc)
        if enriched_doc.embedding is None:
            log.warning(
                "relevance service: document embedding unavailable, doc_id=%s", doc.id
            )
            return None
        vectors = page_embeddings.load_all(embeddings.model_name())
        pages = [
            CandidatePage(path=pv.path, body="", embedding=embeddings.unpack(pv.vector))
            for pv in vectors
        ]
        result = self.evaluate(enriched_doc, pages)
        # Score/count telemetry — the threshold-calibration signal in Grafana.
        ingest_relevance_candidates.observe(len(pages))
        ingest_relevance_kept_pages.observe(len(result.kept))
        for page in result.kept:
            if page.path in result.scores:
                ingest_relevance_scores.labels(decision="kept").observe(result.scores[page.path])
        for page in result.dropped:
            if page.path in result.scores:
                ingest_relevance_scores.labels(decision="dropped").observe(result.scores[page.path])
        # Kept scores + the highest dropped scores (the near-misses) are the
        # signal for calibrating the filter threshold against real traffic.
        near_misses = sorted(
            (round(result.scores[p.path], 4) for p in result.dropped if p.path in result.scores),
            reverse=True,
        )[:5]
        log.info(
            "relevance service: kept %d/%d pages, doc_id=%s kept=%s dropped_near_misses=%s",
            len(result.kept),
            len(pages),
            doc.id,
            [(p.path, round(result.scores[p.path], 4)) for p in result.kept if p.path in result.scores],
            near_misses,
        )
        return result


_service: RelevanceService | None = None


def get_relevance_service() -> RelevanceService:
    """The process-wide service, built lazily on first use so the underlying
    filter (e.g. the two-tower ONNX model) is loaded once, not per document."""
    global _service
    if _service is None:
        _service = RelevanceService(build_relevance_filter())
    return _service
