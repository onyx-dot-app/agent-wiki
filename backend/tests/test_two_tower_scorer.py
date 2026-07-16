"""Tests for the two-tower ONNX scorer.

onnxruntime is mocked with a fake session (patched in ``onnx_model``) — we
assert the scorer's own logic: tile the doc across pages, stack pages, read the
``prob`` output. That the exported graph itself is numerically correct is
covered by the export tool's torch-vs-ONNX parity test, not here.
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pytest

from app.ingest.relevance import Scorer
from app.ingest.relevance import onnx_model
from app.ingest.relevance.two_tower_scorer import TwoTowerScorer


class _FakeSession:
    """Captures the feed and returns a preset ``prob`` array."""

    def __init__(self, probs: list[float], meta: dict[str, str] | None = None) -> None:
        self._probs = probs
        self._meta = meta or {}
        self.feed: dict[str, Any] | None = None

    def run(self, output_names: list[str], feed: dict[str, Any]) -> list[Any]:
        self.feed = feed
        return [np.asarray(self._probs, dtype=np.float32)]

    def get_modelmeta(self) -> Any:
        return type("_Meta", (), {"custom_metadata_map": self._meta})()


def _scorer(
    monkeypatch: pytest.MonkeyPatch, probs: list[float], meta: dict[str, str] | None = None
) -> tuple[TwoTowerScorer, _FakeSession]:
    fake = _FakeSession(probs, meta)
    monkeypatch.setattr(onnx_model.onnxruntime, "InferenceSession", lambda *a, **k: fake)
    return TwoTowerScorer("unused.onnx"), fake


def test_score_batch_returns_probs(monkeypatch: pytest.MonkeyPatch):
    scorer, _ = _scorer(monkeypatch, [0.9, 0.1])
    probs = scorer.score_batch([1.0, 2.0], [[0.1, 0.2], [0.3, 0.4]])
    assert probs == pytest.approx([0.9, 0.1])


def test_tiles_doc_and_stacks_pages(monkeypatch: pytest.MonkeyPatch):
    scorer, fake = _scorer(monkeypatch, [0.5, 0.5])
    scorer.score_batch([1.0, 2.0], [[0.1, 0.2], [0.3, 0.4]])
    assert fake.feed is not None
    # wiki = the candidate pages, stacked
    assert np.allclose(fake.feed["wiki"], [[0.1, 0.2], [0.3, 0.4]])
    # doc = the one document vector tiled across both pages
    assert np.allclose(fake.feed["doc"], [[1.0, 2.0], [1.0, 2.0]])
    assert fake.feed["wiki"].dtype == np.float32


def test_empty_pages_returns_empty_without_running(monkeypatch: pytest.MonkeyPatch):
    scorer, fake = _scorer(monkeypatch, [])
    assert scorer.score_batch([1.0, 2.0], []) == []
    assert fake.feed is None  # never ran the graph


def test_satisfies_scorer_protocol(monkeypatch: pytest.MonkeyPatch):
    scorer, _ = _scorer(monkeypatch, [0.5])
    s: Scorer = scorer  # structural conformance
    assert hasattr(s, "score_batch")


def test_cutoff_read_from_model_metadata(monkeypatch: pytest.MonkeyPatch):
    scorer, _ = _scorer(monkeypatch, [0.5], meta={"cutoff": "0.0016"})
    assert scorer.cutoff == pytest.approx(0.0016)


def test_cutoff_absent_is_none(monkeypatch: pytest.MonkeyPatch):
    scorer, _ = _scorer(monkeypatch, [0.5], meta={})
    assert scorer.cutoff is None
