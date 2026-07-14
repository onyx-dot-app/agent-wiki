"""Cosine-similarity relevance filter — the cold-start model.

No training, no labels, no weights: relevance is the cosine similarity of the
document and page embeddings, thresholded. The embeddings come from the
carriers' ``embedding`` slots (filled by ``app.ingest.enrich``); this filter
only compares them.
"""
from __future__ import annotations

import math
from collections.abc import Sequence

from app.ingest.relevance.filter import RelevanceFilter
from app.ingest.types import CandidatePage, IngestionDocument

# Cosine cutoff at the study's 85%-filter operating point (keep the top 15% of
# candidate pairs) — the 85th percentile of embed_cosine on the offline
# model-selection dataset. Fit on that dataset's candidate population;
# recalibrate against the production score distribution before it gates.
DEFAULT_THRESHOLD = 0.4334


def cosine_similarity_score(a: Sequence[float], b: Sequence[float]) -> float:
    """Cosine similarity of two equal-length vectors, in [-1, 1].

    Returns 0.0 if either vector is all-zeros (undefined direction). ``strict``
    zip raises on a length mismatch rather than silently truncating the dot
    product (which the norms wouldn't match) — callers must pass equal-length
    vectors; the filter guards for that before calling here."""
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


class CosineSimilarityFilter(RelevanceFilter):
    """Relevant when ``cosine_similarity_score(doc.embedding, page.embedding) >= threshold``.

    Fail-open: if either embedding is missing (``None`` — no key, embed failure,
    or a page not yet embedded), the pair is kept. The filter only ever saves
    reconcile cost; it must never cost recall because of an infra miss.
    """

    def __init__(self, threshold: float = DEFAULT_THRESHOLD) -> None:
        self._threshold = threshold

    @property
    def threshold(self) -> float:
        return self._threshold

    def similarity(
        self, doc: IngestionDocument, page: CandidatePage
    ) -> float | None:
        """Cosine of the two embeddings, or ``None`` if either is unavailable.

        Exposed (not just the boolean) so callers can shadow-log scores and
        calibrate the threshold against real traffic.

        A length mismatch (e.g. vectors from different embedding models during a
        migration) is treated like a missing embedding — ``None``, so the pair
        is kept — rather than scored over a truncated dimension count or raised
        (a raise would abort the whole filter, dropping every candidate)."""
        doc_vec, page_vec = doc.embedding, page.embedding
        if doc_vec is None or page_vec is None or len(doc_vec) != len(page_vec):
            return None
        return cosine_similarity_score(doc_vec, page_vec)

    def is_relevant(self, doc: IngestionDocument, page: CandidatePage) -> bool:
        sim = self.similarity(doc, page)
        return True if sim is None else sim >= self._threshold
