"""Tests for the two-tower relevance filter.

No model here — a stub Scorer returns preset probabilities so we can assert the
filter's thresholding and fail-open policy in isolation. The concrete scorer
(which runs the trained network) is a later phase.
"""
from __future__ import annotations

from collections.abc import Sequence

from app.ingest.relevance import TwoTowerFilter
from app.ingest.types import CandidatePage, IngestionDocument


class _StubScorer:
    """Returns a preset list of probabilities regardless of inputs."""

    def __init__(self, probs: list[float]) -> None:
        self._probs = probs
        self.calls = 0

    def score_batch(
        self, doc_vec: Sequence[float], page_vecs: list[Sequence[float]]
    ) -> list[float]:
        self.calls += 1
        return list(self._probs)


class _RaisingScorer:
    def score_batch(
        self, doc_vec: Sequence[float], page_vecs: list[Sequence[float]]
    ) -> list[float]:
        raise RuntimeError("boom")


_DOC = IngestionDocument(content="doc", embedding=[1.0, 0.0])


def _page(path: str, emb: list[float] | None) -> CandidatePage:
    return CandidatePage(path=path, body="b", embedding=emb)


def test_keeps_pairs_at_or_above_threshold():
    f = TwoTowerFilter(_StubScorer([0.9, 0.2]), threshold=0.5)
    pages = [_page("a.md", [0.1, 0.2]), _page("b.md", [0.3, 0.4])]
    assert [p.path for p in f.keep_relevant(_DOC, pages)] == ["a.md"]


def test_threshold_boundary_is_inclusive():
    f = TwoTowerFilter(_StubScorer([0.5]), threshold=0.5)
    assert [p.path for p in f.keep_relevant(_DOC, [_page("a.md", [0.1])])] == ["a.md"]


def test_empty_pages_returns_empty():
    f = TwoTowerFilter(_StubScorer([]), threshold=0.5)
    assert f.keep_relevant(_DOC, []) == []


def test_fail_open_on_scorer_error():
    pages = [_page("a.md", [0.1]), _page("b.md", [0.2])]
    kept = TwoTowerFilter(_RaisingScorer(), threshold=0.99).keep_relevant(_DOC, pages)
    assert kept == pages


def test_fail_open_when_doc_embedding_missing():
    scorer = _StubScorer([0.0])  # would drop everything if consulted
    doc = IngestionDocument(content="doc", embedding=None)
    pages = [_page("a.md", [0.1])]
    kept = TwoTowerFilter(scorer, threshold=0.99).keep_relevant(doc, pages)
    assert kept == pages
    assert scorer.calls == 0  # never scored — no doc vector


def test_fail_open_on_score_count_mismatch():
    # scorer returns 1 prob for 2 scorable pages → malformed → keep all
    pages = [_page("a.md", [0.1]), _page("b.md", [0.2])]
    kept = TwoTowerFilter(_StubScorer([0.9]), threshold=0.5).keep_relevant(_DOC, pages)
    assert kept == pages


def test_pages_without_embedding_are_kept_and_not_scored():
    scorer = _StubScorer([0.1])  # the one embedded page scores below threshold
    no_emb = _page("keep.md", None)
    low = _page("drop.md", [0.3, 0.4])
    kept = TwoTowerFilter(scorer, threshold=0.5).keep_relevant(_DOC, [no_emb, low])
    # unembedded page kept (fail-open); embedded-but-low page dropped
    assert [p.path for p in kept] == ["keep.md"]


def test_is_relevant_single_pair():
    assert TwoTowerFilter(_StubScorer([0.8]), threshold=0.5).is_relevant(
        _DOC, _page("a.md", [0.1])
    ) is True
    assert TwoTowerFilter(_StubScorer([0.3]), threshold=0.5).is_relevant(
        _DOC, _page("a.md", [0.1])
    ) is False
