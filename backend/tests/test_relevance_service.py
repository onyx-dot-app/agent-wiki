"""RelevanceService — enrich + filter + partition into kept/dropped, fail-open.

Enrichment is stubbed (it hits the embedding API / store); the filter is a fake
so the service's own logic — partitioning and fail-open — is what's tested.
"""

from __future__ import annotations

import pytest

from app.ingest import enrich
from app.ingest.relevance.filter import RelevanceFilter
from app.ingest.relevance.service import RelevanceResult, RelevanceService
from app.ingest.types import CandidatePage, IngestionDocument


class _DropByPath(RelevanceFilter):
    """Keeps every page except those in ``drop``."""

    def __init__(self, drop: set[str]) -> None:
        self._drop = drop

    def is_relevant(self, doc: IngestionDocument, page: CandidatePage) -> bool:
        return page.path not in self._drop


class _Boom(RelevanceFilter):
    def is_relevant(self, doc: IngestionDocument, page: CandidatePage) -> bool:
        raise RuntimeError("scorer exploded")


class _ScoredFilter(RelevanceFilter):
    """Fake scoring filter: per-path scores; a None score = unscorable (kept)."""

    def __init__(self, scores: dict[str, float | None], cutoff: float) -> None:
        self._scores = scores
        self._cutoff = cutoff

    def is_relevant(self, doc: IngestionDocument, page: CandidatePage) -> bool:
        s = self._scores.get(page.path)
        return s is None or s >= self._cutoff

    def score_pages(
        self, doc: IngestionDocument, pages: list[CandidatePage]
    ) -> list[float | None] | None:
        return [self._scores.get(p.path) for p in pages]


@pytest.fixture(autouse=True)
def _no_embedding_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    """Enrichment hits the embedding API / store; make it a pass-through so the
    filter decision is what's under test."""
    monkeypatch.setattr(enrich, "with_document_embedding", lambda d: d)
    monkeypatch.setattr(enrich, "with_page_embeddings", lambda ps: ps)


def _doc() -> IngestionDocument:
    return IngestionDocument(content="hello", id="d1", title="T")


def _pages(*paths: str) -> list[CandidatePage]:
    return [CandidatePage(path=p, body=f"body {p}") for p in paths]


def test_partitions_kept_and_dropped_in_input_order() -> None:
    svc = RelevanceService(_DropByPath({"b.md"}))
    pages = _pages("a.md", "b.md", "c.md")

    result = svc.evaluate(_doc(), pages)

    assert [p.path for p in result.kept] == ["a.md", "c.md"]
    assert [p.path for p in result.dropped] == ["b.md"]
    # Returns the caller's own objects.
    assert result.kept[0] is pages[0]
    assert result.dropped[0] is pages[1]


def test_empty_pages_is_empty_result() -> None:
    result = RelevanceService(_DropByPath(set())).evaluate(_doc(), [])
    assert result == RelevanceResult(kept=[], dropped=[])


def test_keep_all_when_filter_keeps_all() -> None:
    result = RelevanceService(_DropByPath(set())).evaluate(_doc(), _pages("a.md", "b.md"))
    assert [p.path for p in result.kept] == ["a.md", "b.md"]
    assert result.dropped == []


def test_fails_open_on_filter_error() -> None:
    pages = _pages("a.md", "b.md")
    result = RelevanceService(_Boom()).evaluate(_doc(), pages)
    # Any failure keeps every page — never drops a candidate.
    assert result.kept == pages
    assert result.dropped == []


def test_fails_open_on_enrich_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(_d: IngestionDocument) -> IngestionDocument:
        raise RuntimeError("embed API down")

    monkeypatch.setattr(enrich, "with_document_embedding", boom)
    pages = _pages("a.md", "b.md")
    result = RelevanceService(_DropByPath({"a.md"})).evaluate(_doc(), pages)
    assert result.kept == pages and result.dropped == []


def test_kept_ordered_most_relevant_first_with_scores_exposed() -> None:
    svc = RelevanceService(
        _ScoredFilter({"lo.md": 0.2, "hi.md": 0.9, "mid.md": 0.5, "out.md": 0.01}, cutoff=0.1)
    )
    result = svc.evaluate(_doc(), _pages("lo.md", "hi.md", "mid.md", "out.md"))

    # Kept sorted by score descending; the below-cutoff page dropped.
    assert [p.path for p in result.kept] == ["hi.md", "mid.md", "lo.md"]
    assert [p.path for p in result.dropped] == ["out.md"]
    # Every scored pair is exposed — including dropped ones (calibration data).
    assert result.scores == {"lo.md": 0.2, "hi.md": 0.9, "mid.md": 0.5, "out.md": 0.01}


def test_unscored_kept_pages_sort_last_in_input_order() -> None:
    svc = RelevanceService(
        _ScoredFilter({"a.md": 0.3, "n1.md": None, "b.md": 0.8, "n2.md": None}, cutoff=0.1)
    )
    result = svc.evaluate(_doc(), _pages("a.md", "n1.md", "b.md", "n2.md"))
    # Scored first (desc), then fail-open unscored pages in input order.
    assert [p.path for p in result.kept] == ["b.md", "a.md", "n1.md", "n2.md"]
    assert set(result.scores) == {"a.md", "b.md"}  # None entries not exposed


def test_scoreless_filter_keeps_input_order_and_empty_scores() -> None:
    result = RelevanceService(_DropByPath(set())).evaluate(_doc(), _pages("a.md", "b.md"))
    assert [p.path for p in result.kept] == ["a.md", "b.md"]
    assert result.scores == {}
