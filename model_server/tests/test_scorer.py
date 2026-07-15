"""Tests for the bundle loader + scorer, over synthetic bundles (see conftest)."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

import pytest

from two_tower.bundle import load_inference_bundle
from two_tower.scorer import BundleScorer


def test_load_and_score_roundtrip(make_bundle: Callable[..., Path], embed_dim: int):
    scorer = BundleScorer.load(make_bundle(cutoff=0.4))
    assert scorer.cutoff == 0.4

    probs = scorer.score_batch(
        [0.1] * embed_dim, [[0.2] * embed_dim, [0.3] * embed_dim, [0.4] * embed_dim]
    )
    assert len(probs) == 3
    assert all(0.0 <= p <= 1.0 for p in probs)


def test_state_dict_layers(make_bundle: Callable[..., Path]):
    model = load_inference_bundle(make_bundle()).model
    layers = {name.split(".")[0] for name, _ in model.named_parameters()}
    assert layers == {"input_proj", "hidden", "ffn1", "classifier"}


def test_empty_pages_returns_empty(make_bundle: Callable[..., Path], embed_dim: int):
    assert BundleScorer.load(make_bundle()).score_batch([0.1] * embed_dim, []) == []


def test_cutoff_may_be_absent(make_bundle: Callable[..., Path]):
    assert BundleScorer.load(make_bundle(cutoff=None)).cutoff is None


@pytest.mark.parametrize(
    "overrides",
    [
        {"use_source_type": True, "num_source_types": 3},
        {"interaction_features": True},
        {"use_input_proj": False},
        {"use_ffn1": False},
    ],
)
def test_rejects_unsupported_bundle(make_bundle: Callable[..., Path], overrides: dict[str, Any]):
    with pytest.raises(ValueError, match="not the supported architecture"):
        BundleScorer.load(make_bundle(**overrides))


def test_rejects_wrong_model_class(make_bundle: Callable[..., Path]):
    with pytest.raises(ValueError, match="unsupported model_class"):
        BundleScorer.load(make_bundle(model_class="SomethingElse"))


def test_rejects_wrong_format_version(make_bundle: Callable[..., Path]):
    with pytest.raises(ValueError, match="format_version"):
        BundleScorer.load(make_bundle(format_version=2))
