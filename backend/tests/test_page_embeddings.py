"""Unit tests for the Phase-0 embedding foundation (pure — no DB / network).

Covers the vector pack/unpack round-trip, the content-hash guard, and that the
feature is a safe no-op when disabled (the default), so shipping it changes no
behavior until an operator opts in.
"""
from __future__ import annotations

from app.llm import embeddings


def test_pack_unpack_roundtrip() -> None:
    vec = [0.125, -0.5, 0.0, 1.5, -2.25]
    out = embeddings.unpack(embeddings.pack(vec))
    assert len(out) == len(vec)
    for a, b in zip(out, vec):
        assert abs(a - b) < 1e-6  # float32 exact for these values


def test_content_sha256_is_stable_and_distinct() -> None:
    assert embeddings.content_sha256("hello") == embeddings.content_sha256("hello")
    assert embeddings.content_sha256("a") != embeddings.content_sha256("b")


def test_disabled_by_default_is_a_noop() -> None:
    # Default config has ingest embeddings off (and test env has no OpenAI key),
    # so the client is never called and every entry point is best-effort None.
    assert embeddings.available() is False
    assert embeddings.embed_texts(["anything"]) is None
    assert embeddings.embed_text("anything") is None


def test_model_name_has_a_default() -> None:
    assert embeddings.model_name()  # non-empty; defaults to text-embedding-3-small
