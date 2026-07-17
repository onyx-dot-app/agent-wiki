"""Two-tower relevance filter — the warm, per-deployment model.

Unlike the cosine filter (parameter-free, cold-start), this scores each
(document, page) pair with a trained two-tower network that outputs
P(relevant), then thresholds it. The network is **not** run here: scoring is
delegated to a :class:`Scorer`, so the filter is agnostic to *how* and *where*
the model executes — a remote model-server call, an in-process runtime, etc.

A deployment plugs in a Scorer only after training a model on its own data;
until then it runs the cosine filter. Trained weights are per-deployment and
are never shipped to other deployments.

Fail-open throughout: a missing embedding, a scorer error, or a malformed score
list keeps the affected candidates, so the filter only ever *saves* reconcile
cost and never *costs* recall on an infra miss.
"""
from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Protocol

from app.ingest.relevance.filter import RelevanceFilter
from app.ingest.types import CandidatePage, IngestionDocument

log = logging.getLogger(__name__)


class Scorer(Protocol):
    """Turns embedding pairs into relevance probabilities — the execution
    boundary for the two-tower model.

    Implementations run the trained network however they choose (a remote
    model server, an in-process runtime, ...). :class:`TwoTowerFilter` depends
    only on this one call, so the serving strategy can change without touching
    the filter.
    """

    def score_batch(
        self, doc_vec: Sequence[float], page_vecs: list[Sequence[float]]
    ) -> list[float]:
        """P(relevant) for each ``(doc_vec, page_vec)`` pair — one score per
        entry in ``page_vecs``, in the same order."""
        ...


class TwoTowerFilter(RelevanceFilter):
    """Relevant when the trained two-tower's P(relevant) >= ``threshold``.

    Owns only batching, thresholding, and the fail-open policy; the forward
    pass lives behind :class:`Scorer`. ``threshold`` is a probability in [0, 1]
    and is deployment-specific (calibrated per trained model), so there is no
    default — a caller must choose one.
    """

    def __init__(self, scorer: Scorer, threshold: float) -> None:
        self._scorer = scorer
        self._threshold = threshold

    @property
    def threshold(self) -> float:
        return self._threshold

    def is_relevant(self, doc: IngestionDocument, page: CandidatePage) -> bool:
        return bool(self.keep_relevant(doc, [page]))

    def keep_relevant(
        self, doc: IngestionDocument, pages: list[CandidatePage]
    ) -> list[CandidatePage]:
        if not pages:
            return []
        scores = self.score_pages(doc, pages)
        if scores is None:
            return list(pages)  # nothing scorable / scorer failure → keep all
        # An unscored page (missing embedding) is kept — fail-open.
        return [
            page
            for page, score in zip(pages, scores, strict=True)
            if score is None or score >= self._threshold
        ]

    def score_pages(
        self, doc: IngestionDocument, pages: list[CandidatePage]
    ) -> list[float | None] | None:
        """P(relevant) per page; a ``None`` entry for a page without an
        embedding. Returns ``None`` outright when nothing is scorable (no
        document vector, no embedded page) or the scorer fails — the keep
        decision fails open on that."""
        if not pages:
            return []
        doc_vec = doc.embedding
        if doc_vec is None:
            return None

        # Score only the pages that have an embedding.
        idx: list[int] = []
        page_vecs: list[Sequence[float]] = []
        for i, page in enumerate(pages):
            vec = page.embedding
            if vec is not None:
                idx.append(i)
                page_vecs.append(vec)
        if not page_vecs:
            return None

        try:
            probs = self._scorer.score_batch(doc_vec, page_vecs)
            if len(probs) != len(page_vecs):
                raise ValueError(
                    f"scorer returned {len(probs)} scores for {len(page_vecs)} pairs"
                )
        except Exception:
            log.warning(
                "two-tower scoring failed or returned malformed scores; keeping all candidates",
                exc_info=True,
            )
            return None

        scores: list[float | None] = [None] * len(pages)
        for k, prob in enumerate(probs):
            scores[idx[k]] = prob
        return scores
