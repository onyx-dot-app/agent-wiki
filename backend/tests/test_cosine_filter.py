"""Tests for the cosine relevance filter and the embedding enrichment stage.

Pure unit tests — the enrichment helpers are exercised with the embedding
client and the page-embedding store monkeypatched, so nothing here touches
OpenAI or the database.
"""
from __future__ import annotations

import pytest

from app.ingest import enrich
from app.ingest.relevance import CosineSimilarityFilter
from app.ingest.relevance.cosine_filter import cosine_similarity_score
from app.ingest.types import CandidatePage, IngestionDocument


# --------------------------------------------------------------------------- #
# cosine_similarity_score()                                                                    #
# --------------------------------------------------------------------------- #


def test_cosine_identical_is_one():
    assert cosine_similarity_score([1.0, 2.0, 3.0], [1.0, 2.0, 3.0]) == pytest.approx(1.0)


def test_cosine_orthogonal_is_zero():
    assert cosine_similarity_score([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)


def test_cosine_opposite_is_minus_one():
    assert cosine_similarity_score([1.0, 1.0], [-1.0, -1.0]) == pytest.approx(-1.0)


def test_cosine_zero_vector_is_zero():
    assert cosine_similarity_score([0.0, 0.0], [1.0, 2.0]) == 0.0


def test_cosine_raises_on_length_mismatch():
    # The pure helper is strict — a length mismatch is a loud error, never a
    # silently-truncated (wrong) similarity.
    with pytest.raises(ValueError):
        cosine_similarity_score([1.0, 2.0], [1.0, 2.0, 3.0])


# --------------------------------------------------------------------------- #
# CosineSimilarityFilter                                                      #
# --------------------------------------------------------------------------- #

_UNIT = [1.0, 0.0]


def _doc(vec):
    return IngestionDocument(content="x", embedding=vec)


def _page(vec):
    return CandidatePage(path="p.md", body="b", embedding=vec)


def test_relevant_at_and_above_threshold():
    f = CosineSimilarityFilter(threshold=0.5)
    # identical vectors → cosine 1.0 ≥ 0.5
    assert f.is_relevant(_doc(_UNIT), _page(_UNIT)) is True


def test_not_relevant_below_threshold():
    f = CosineSimilarityFilter(threshold=0.5)
    # orthogonal → cosine 0.0 < 0.5
    assert f.is_relevant(_doc([1.0, 0.0]), _page([0.0, 1.0])) is False


def test_threshold_boundary_is_inclusive():
    # cosine of these is exactly cos(45°) ≈ 0.7071; set threshold to match.
    sim = cosine_similarity_score([1.0, 0.0], [1.0, 1.0])
    f = CosineSimilarityFilter(threshold=sim)
    assert f.is_relevant(_doc([1.0, 0.0]), _page([1.0, 1.0])) is True


def test_fail_open_when_doc_embedding_missing():
    f = CosineSimilarityFilter(threshold=0.99)
    assert f.is_relevant(_doc(None), _page(_UNIT)) is True
    assert f.similarity(_doc(None), _page(_UNIT)) is None


def test_fail_open_when_page_embedding_missing():
    f = CosineSimilarityFilter(threshold=0.99)
    assert f.is_relevant(_doc(_UNIT), _page(None)) is True


def test_fail_open_on_length_mismatch():
    # Mismatched dims (e.g. a partially-migrated store) → can't compare → keep.
    f = CosineSimilarityFilter(threshold=0.99)
    doc = _doc([1.0, 0.0, 0.0])
    page = _page([1.0, 0.0])
    assert f.similarity(doc, page) is None
    assert f.is_relevant(doc, page) is True


def test_keep_relevant_filters_the_batch():
    f = CosineSimilarityFilter(threshold=0.5)
    doc = _doc([1.0, 0.0])
    close = CandidatePage(path="close.md", body="b", embedding=[1.0, 0.0])   # cos 1.0
    far = CandidatePage(path="far.md", body="b", embedding=[0.0, 1.0])       # cos 0.0
    kept = f.keep_relevant(doc, [close, far])
    assert [p.path for p in kept] == ["close.md"]


# --------------------------------------------------------------------------- #
# enrichment                                                                  #
# --------------------------------------------------------------------------- #


def test_with_document_embedding_fills_slot(monkeypatch):
    monkeypatch.setattr(enrich.embeddings, "embed_text", lambda text: [0.1, 0.2])
    out = enrich.with_document_embedding(IngestionDocument(content="hello"))
    assert out.embedding == [0.1, 0.2]


def test_with_document_embedding_noop_when_unavailable(monkeypatch):
    monkeypatch.setattr(enrich.embeddings, "embed_text", lambda text: None)
    out = enrich.with_document_embedding(IngestionDocument(content="hello"))
    assert out.embedding is None


def test_with_document_embedding_skips_when_already_set(monkeypatch):
    def _boom(text):
        raise AssertionError("should not re-embed")

    monkeypatch.setattr(enrich.embeddings, "embed_text", _boom)
    doc = IngestionDocument(content="hello", embedding=[9.0])
    assert enrich.with_document_embedding(doc).embedding == [9.0]


def test_with_page_embeddings_fills_from_store(monkeypatch):
    # store returns a packed vector for "a.md" only; "b.md" stays None.
    packed = enrich.embeddings.pack([0.3, 0.4])
    monkeypatch.setattr(
        enrich.page_embeddings, "load_paths", lambda paths: {"a.md": packed}
    )
    pages = [CandidatePage(path="a.md", body="x"), CandidatePage(path="b.md", body="y")]
    out = {p.path: p.embedding for p in enrich.with_page_embeddings(pages)}
    assert out["a.md"] == pytest.approx([0.3, 0.4])
    assert out["b.md"] is None


def test_score_pages_similarities_with_none_for_missing_embedding():
    f = CosineSimilarityFilter(threshold=0.5)
    pages = [_page(_UNIT), _page(None)]
    scores = f.score_pages(_doc(_UNIT), pages)
    assert scores is not None
    assert scores[0] == pytest.approx(1.0)
    assert scores[1] is None
