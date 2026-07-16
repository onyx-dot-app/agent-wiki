"""Shadow-mode relevance filter in the ingest reconcile.

`_shadow_relevance_filter` must record what the filter *would* drop (metric +
`filtered_by_relevance` eval sample) without changing the candidate set. It is
best-effort and fail-open.
"""

from __future__ import annotations

import pytest
from prometheus_client import REGISTRY
from sqlalchemy import select

from app.db.fts import SearchHit
from app.db.models import IngestEvalSample
from app.db.session import session
from app.ingest.models import WikiUpdateCandidate
from app.ingest.relevance.filter import RelevanceFilter
from app.ingest.types import CandidatePage, IngestionDocument
from app.tasks import wiki_update


class _DropByPath(RelevanceFilter):
    """Fake filter: keeps every page except those in ``drop``."""

    def __init__(self, drop: set[str]) -> None:
        self._drop = drop

    def is_relevant(self, doc: IngestionDocument, page: CandidatePage) -> bool:
        return page.path not in self._drop


def _candidate(path: str, score: float = 5.0) -> WikiUpdateCandidate:
    hit = SearchHit(doc_id=path, path=path, title=None, snippet="", score=score)
    return WikiUpdateCandidate(hit=hit, body=f"body of {path}")


@pytest.fixture(autouse=True)
def _no_embedding_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    """Enrichment hits the OpenAI API / embedding store; the filter decision is
    what's under test, so make enrichment a pass-through."""
    monkeypatch.setattr(wiki_update.ingest_enrich, "with_document_embedding", lambda d: d)
    monkeypatch.setattr(wiki_update.ingest_enrich, "with_page_embeddings", lambda ps: ps)


def _shadow_rows(paths: set[str]) -> set[str]:
    with session() as s:
        return {
            r.wiki_path
            for r in s.scalars(
                select(IngestEvalSample).where(
                    IngestEvalSample.outcome == "filtered_by_relevance"
                )
            ).all()
            if r.wiki_path in paths
        }


def test_shadow_records_would_be_drops_only(tmp_db, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(wiki_update, "build_relevance_filter", lambda: _DropByPath({"a.md"}))
    cands = [_candidate("a.md"), _candidate("b.md")]

    before = REGISTRY.get_sample_value(
        "ingest_relevance_shadow_total", {"decision": "dropped", "wiki_path": "a.md"}
    ) or 0.0

    wiki_update._shadow_relevance_filter(
        {"source_document_id": "d1"}, cands,
        source_type="slack", title="T", url="", content="hello",
    )

    # Only a.md is recorded as a would-be drop; b.md is not.
    assert _shadow_rows({"a.md", "b.md"}) == {"a.md"}

    after = REGISTRY.get_sample_value(
        "ingest_relevance_shadow_total", {"decision": "dropped", "wiki_path": "a.md"}
    )
    assert after == before + 1
    assert REGISTRY.get_sample_value(
        "ingest_relevance_shadow_total", {"decision": "kept", "wiki_path": "b.md"}
    )


def test_shadow_fails_open_on_filter_error(tmp_db, monkeypatch: pytest.MonkeyPatch):
    def _boom() -> RelevanceFilter:
        raise RuntimeError("model load blew up")

    monkeypatch.setattr(wiki_update, "build_relevance_filter", _boom)
    # Must not raise, and must record nothing.
    wiki_update._shadow_relevance_filter(
        {"source_document_id": "d2"}, [_candidate("c.md")],
        source_type=None, title=None, url="", content="x",
    )
    assert _shadow_rows({"c.md"}) == set()
