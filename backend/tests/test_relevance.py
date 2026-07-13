"""Contract tests for the relevance-filter interface.

No model here — a stub subclass returns a fixed verdict so we can assert the
shared batch/abstract behavior in isolation.
"""
from __future__ import annotations

import pytest

from app.ingest.relevance import RelevanceFilter
from app.ingest.types import CandidatePage, IngestionDocument


class _StubFilter(RelevanceFilter):
    """Returns a preset verdict regardless of inputs."""

    def __init__(self, verdict: bool) -> None:
        self._verdict = verdict

    def is_relevant(self, doc: IngestionDocument, page: CandidatePage) -> bool:
        return self._verdict


_DOC = IngestionDocument(content="release notes", source_type="github")
_PAGES = [
    CandidatePage(path="a.md", body="alpha"),
    CandidatePage(path="b.md", body="beta"),
]


def test_cannot_instantiate_abstract_base():
    with pytest.raises(TypeError):
        RelevanceFilter()  # type: ignore[abstract]


def test_keep_relevant_returns_all_when_relevant():
    assert _StubFilter(True).keep_relevant(_DOC, _PAGES) == _PAGES


def test_keep_relevant_returns_none_when_irrelevant():
    assert _StubFilter(False).keep_relevant(_DOC, _PAGES) == []


def test_is_relevant_verdict_is_used():
    assert _StubFilter(True).is_relevant(_DOC, _PAGES[0]) is True
    assert _StubFilter(False).is_relevant(_DOC, _PAGES[0]) is False
