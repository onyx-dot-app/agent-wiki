"""Tests for the relevance-filter factory — which filter, and its threshold."""
from __future__ import annotations

from pathlib import Path

import pytest

from app.config import Config
from app.ingest.relevance import (
    CosineSimilarityFilter,
    TwoTowerFilter,
    build_relevance_filter,
)
from app.ingest.relevance import onnx_model
from app.ingest.relevance.factory import _select


class _FakeMeta:
    def __init__(self, custom: dict[str, str]) -> None:
        self.custom_metadata_map = custom


class _FakeSession:
    def __init__(self, meta: dict[str, str]) -> None:
        self._meta = meta

    def get_modelmeta(self) -> _FakeMeta:
        return _FakeMeta(self._meta)


def _use_model(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, meta: dict[str, str]) -> str:
    """Point the loader at a fake ONNX session carrying ``meta``; return a real path."""
    monkeypatch.setattr(
        onnx_model.onnxruntime, "InferenceSession", lambda *a, **k: _FakeSession(meta)
    )
    path = tmp_path / "model.onnx"
    path.write_bytes(b"x")
    return str(path)


def test_cosine_when_no_model_path():
    f = _select(model_path="", cosine_threshold=0.42)
    assert isinstance(f, CosineSimilarityFilter)
    assert f.threshold == 0.42


def test_cosine_when_model_path_missing():
    f = _select(model_path="/no/such/model.onnx", cosine_threshold=0.42)
    assert isinstance(f, CosineSimilarityFilter)


def test_two_tower_uses_model_cutoff(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    path = _use_model(monkeypatch, tmp_path, {"cutoff": "0.25"})
    f = _select(model_path=path, cosine_threshold=0.42)
    assert isinstance(f, TwoTowerFilter)
    assert f.threshold == 0.25  # the model's embedded cutoff is the source of truth


def test_no_cutoff_keeps_all(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    path = _use_model(monkeypatch, tmp_path, {})  # misconfigured: model carries no cutoff
    f = _select(model_path=path, cosine_threshold=0.42)
    assert isinstance(f, TwoTowerFilter)
    assert f.threshold == 0.0  # fail open — keep everything


def test_build_defaults_to_cosine(tmp_config: Config):
    # With no model path configured, the factory falls back to cosine. Pass the
    # fixture config (model_path="") so the test doesn't read the ambient CONFIG,
    # which a CI worker may point at a real model.
    assert isinstance(build_relevance_filter(tmp_config), CosineSimilarityFilter)
