"""Load the trained two-tower model from a portable bundle file.

A bundle is one ``torch.save``d dict (``*.inference.pt``) holding ``arch`` (the
constructor dims), ``state_dict`` (the trained weights), ``embedding_model``,
and a default ``cutoff`` (P(update) threshold). The weights are never committed
to this repo — the bundle is per-deployment, supplied at runtime from an
artifact store / mounted path.

This server runs a single architecture — concat[wiki, doc] -> input_proj ->
hidden -> ffn1 -> classifier. A bundle whose ``arch`` needs a feature this class
doesn't implement (interaction, source-type, facts, extra features) is rejected
on load.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import torch

from two_tower.model import TwoTowerClassifier

# The bundle layout this loader understands.
_FORMAT_VERSION = 1


@dataclass
class LoadedBundle:
    model: TwoTowerClassifier  # rebuilt, in eval() mode
    embedding_model: str  # which embedding model produced the input vectors
    cutoff: float | None  # bundle default P(update) threshold (None => caller must set)


# arch flags the served architecture requires on / off. It is the concat head
# with an input projection and one FFN; a bundle needing anything else (a
# different fusion, source-type index, facts, extra/section features) isn't a
# model this class can run.
_REQUIRED_ON = ("use_full_doc_vector", "use_input_proj", "use_ffn1")
_REQUIRED_OFF = (
    "interaction_features",
    "embedding_transformation",
    "use_source_type",
    "use_fact_vector",
    "use_ffn2",
)
_REQUIRED_ZERO = ("num_extra_features", "num_post_ffn_features")


def _assert_supported_arch(arch: dict[str, Any]) -> None:
    problems = [f"{f} is off" for f in _REQUIRED_ON if not arch.get(f)]
    problems += [f"{f} is on" for f in _REQUIRED_OFF if arch.get(f)]
    problems += [f"{f} > 0" for f in _REQUIRED_ZERO if arch.get(f, 0)]
    if problems:
        raise ValueError(
            "bundle is not the supported architecture (concat[wiki, doc] -> "
            "input_proj -> hidden -> ffn1 -> classifier):\n  - " + "\n  - ".join(problems)
        )


def build_two_tower_from_arch(arch: dict[str, Any]) -> TwoTowerClassifier:
    """Reconstruct the network from a bundle's ``arch`` dict."""
    _assert_supported_arch(arch)
    return TwoTowerClassifier(
        arch["embed_dim"],
        arch["input_proj_dim"],
        arch["hidden_dim"],
        arch["ffn1_dim"],
        num_classes=arch.get("num_classes", 2),
        dropout=arch.get("dropout", 0.5),
    )


def load_inference_bundle(path: Path, *, map_location: str = "cpu") -> LoadedBundle:
    """Load a bundle file and return a ready-to-serve model + serving config."""
    # weights_only=True forbids arbitrary pickle execution at load time; the
    # bundle holds only tensors + primitive types, so it still loads.
    raw = torch.load(Path(path), map_location=map_location, weights_only=True)
    if raw.get("model_class") != "TwoTowerClassifier":
        raise ValueError(f"unsupported model_class in bundle: {raw.get('model_class')!r}")
    if raw.get("format_version") != _FORMAT_VERSION:
        raise ValueError(
            f"unsupported bundle format_version: {raw.get('format_version')!r} "
            f"(expected {_FORMAT_VERSION})"
        )
    model = build_two_tower_from_arch(raw["arch"])
    model.load_state_dict(raw["state_dict"], strict=True)
    model.eval()
    return LoadedBundle(
        model=model,
        embedding_model=raw["embedding_model"],
        cutoff=raw.get("cutoff"),
    )
