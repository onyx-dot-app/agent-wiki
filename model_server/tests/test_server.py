"""Tests for the FastAPI model server (endpoints + startup)."""
from __future__ import annotations

from pathlib import Path
from typing import Callable

import pytest
from fastapi.testclient import TestClient

import server


def test_health_and_score(
    make_bundle: Callable[..., Path], embed_dim: int, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv(server.MODEL_BUNDLE_PATH_ENV, str(make_bundle()))
    with TestClient(server.app) as client:  # enter -> lifespan loads the bundle
        health = client.get("/health")
        assert health.status_code == 200
        assert health.json() == {"ready": True}

        resp = client.post(
            "/score",
            json={"doc_vec": [0.1] * embed_dim, "page_vecs": [[0.2] * embed_dim, [0.3] * embed_dim]},
        )
        assert resp.status_code == 200
        probs = resp.json()["probs"]
        assert len(probs) == 2
        assert all(0.0 <= p <= 1.0 for p in probs)


def test_startup_fails_without_bundle_path(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv(server.MODEL_BUNDLE_PATH_ENV, raising=False)
    with pytest.raises(RuntimeError, match=server.MODEL_BUNDLE_PATH_ENV):
        with TestClient(server.app):
            pass
