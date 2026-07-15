"""Shared test fixtures: build synthetic bundles in the on-disk format.

A tiny model is constructed and ``torch.save``d in the bundle layout, so the
load → score path is exercised end-to-end without the real weights.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

import pytest
import torch

from two_tower.model import TwoTowerClassifier

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


@pytest.fixture
def embed_dim() -> int:
    return EMBED_DIM


@pytest.fixture
def make_bundle(tmp_path: Path) -> Callable[..., Path]:
    """Write a bundle file and return its path.

    Keyword args ``cutoff`` / ``format_version`` / ``model_class`` set the
    bundle envelope; any other kwargs flip ``arch`` flags (e.g.
    ``make_bundle(use_source_type=True)`` for a non-servable bundle).
    """

    def _make(
        *,
        cutoff: float | None = 0.4,
        format_version: int = 1,
        model_class: str = "TwoTowerClassifier",
        name: str = "model.inference.pt",
        **arch_overrides: Any,
    ) -> Path:
        arch = _arch(**arch_overrides)
        model = TwoTowerClassifier(
            arch["embed_dim"],
            arch["input_proj_dim"],
            arch["hidden_dim"],
            arch["ffn1_dim"],
            num_classes=arch["num_classes"],
            dropout=arch["dropout"],
        )
        path = tmp_path / name
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
        return path

    return _make
