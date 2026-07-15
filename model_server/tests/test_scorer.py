"""Round-trip tests for the two-tower bundle loader + scorer.

A tiny synthetic model is built, saved in the bundle format, and loaded back —
so the load → score path is exercised end-to-end without the real weights.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import torch

from two_tower.bundle import load_inference_bundle
from two_tower.model import TwoTowerClassifier
from two_tower.scorer import BundleScorer

EMBED_DIM = 8


def _arch(**overrides: Any) -> dict[str, Any]:
    """A supported arch dict (tiny dims); overrides flip individual flags."""
    arch = {
        "embed_dim": EMBED_DIM,
        "input_proj_dim": 6,
        "hidden_dim": 4,
        "ffn1_dim": 4,
        "num_classes": 2,
        "dropout": 0.5,
        # concat head with an input projection + one FFN; nothing else.
        "use_full_doc_vector": True,
        "use_input_proj": True,
        "use_ffn1": True,
        "interaction_features": False,
        "embedding_transformation": False,
        "use_source_type": False,
        "use_fact_vector": False,
        "use_ffn2": False,
        "num_extra_features": 0,
        "num_post_ffn_features": 0,
    }
    arch.update(overrides)
    return arch


def _save_bundle(
    path: Path,
    arch: dict[str, Any],
    *,
    cutoff: float | None = 0.4,
    format_version: int = 1,
    model_class: str = "TwoTowerClassifier",
) -> None:
    model = TwoTowerClassifier(
        arch["embed_dim"],
        arch["input_proj_dim"],
        arch["hidden_dim"],
        arch["ffn1_dim"],
        num_classes=arch.get("num_classes", 2),
        dropout=arch.get("dropout", 0.5),
    )
    torch.save(
        {
            "format_version": format_version,
            "model_class": model_class,
            "arch": arch,
            "state_dict": model.state_dict(),
            "embedding_model": "text-embedding-3-small",
            "cutoff": cutoff,
        },
        path,
    )


def test_load_and_score_roundtrip(tmp_path: Path):
    path = tmp_path / "tiny.inference.pt"
    _save_bundle(path, _arch(), cutoff=0.4)

    scorer = BundleScorer.load(path)
    assert scorer.cutoff == 0.4

    probs = scorer.score_batch(
        [0.1] * EMBED_DIM, [[0.2] * EMBED_DIM, [0.3] * EMBED_DIM, [0.4] * EMBED_DIM]
    )
    assert len(probs) == 3
    assert all(0.0 <= p <= 1.0 for p in probs)


def test_state_dict_layers(tmp_path: Path):
    path = tmp_path / "tiny.inference.pt"
    _save_bundle(path, _arch())
    model = load_inference_bundle(path).model
    layers = {name.split(".")[0] for name, _ in model.named_parameters()}
    assert layers == {"input_proj", "hidden", "ffn1", "classifier"}


def test_empty_pages_returns_empty(tmp_path: Path):
    path = tmp_path / "tiny.inference.pt"
    _save_bundle(path, _arch())
    assert BundleScorer.load(path).score_batch([0.1] * EMBED_DIM, []) == []


def test_cutoff_may_be_absent(tmp_path: Path):
    path = tmp_path / "tiny.inference.pt"
    _save_bundle(path, _arch(), cutoff=None)
    assert BundleScorer.load(path).cutoff is None


@pytest.mark.parametrize(
    "overrides",
    [
        {"use_source_type": True, "num_source_types": 3},
        {"interaction_features": True},
        {"use_input_proj": False},
        {"use_ffn1": False},
    ],
)
def test_rejects_unsupported_bundle(tmp_path: Path, overrides: dict[str, Any]):
    # A bundle needing a feature this class doesn't implement is refused on load.
    path = tmp_path / "bad.inference.pt"
    _save_bundle(path, _arch(**overrides))
    with pytest.raises(ValueError, match="not the supported architecture"):
        BundleScorer.load(path)


def test_rejects_wrong_model_class(tmp_path: Path):
    path = tmp_path / "bad.inference.pt"
    _save_bundle(path, _arch(), model_class="SomethingElse")
    with pytest.raises(ValueError, match="unsupported model_class"):
        BundleScorer.load(path)


def test_rejects_wrong_format_version(tmp_path: Path):
    path = tmp_path / "bad.inference.pt"
    _save_bundle(path, _arch(), format_version=2)
    with pytest.raises(ValueError, match="format_version"):
        BundleScorer.load(path)
