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


def test_partitions_kept_and_dropped_in_input_order():
    svc = RelevanceService(_DropByPath({"b.md"}))
    pages = _pages("a.md", "b.md", "c.md")

    result = svc.evaluate(_doc(), pages)

    assert [p.path for p in result.kept] == ["a.md", "c.md"]
    assert [p.path for p in result.dropped] == ["b.md"]
    # Returns the caller's own objects.
    assert result.kept[0] is pages[0]
    assert result.dropped[0] is pages[1]


def test_empty_pages_is_empty_result():
    result = RelevanceService(_DropByPath(set())).evaluate(_doc(), [])
    assert result == RelevanceResult(kept=[], dropped=[])


def test_keep_all_when_filter_keeps_all():
    result = RelevanceService(_DropByPath(set())).evaluate(_doc(), _pages("a.md", "b.md"))
    assert [p.path for p in result.kept] == ["a.md", "b.md"]
    assert result.dropped == []


def test_fails_open_on_filter_error():
    pages = _pages("a.md", "b.md")
    result = RelevanceService(_Boom()).evaluate(_doc(), pages)
    # Any failure keeps every page — never drops a candidate.
    assert result.kept == pages
    assert result.dropped == []


def test_fails_open_on_enrich_error(monkeypatch: pytest.MonkeyPatch):
    def boom(_d: IngestionDocument) -> IngestionDocument:
        raise RuntimeError("embed API down")

    monkeypatch.setattr(enrich, "with_document_embedding", boom)
    pages = _pages("a.md", "b.md")
    result = RelevanceService(_DropByPath({"a.md"})).evaluate(_doc(), pages)
    assert result.kept == pages and result.dropped == []
