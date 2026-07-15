"""Two-tower relevance model: architecture, bundle loading, and scoring.

The model *architecture* lives here; the trained *weights* load at runtime from
a bundle file and are never in this repo.
"""
from two_tower.bundle import LoadedBundle, load_inference_bundle
from two_tower.model import TwoTowerClassifier
from two_tower.scorer import BundleScorer

__all__ = [
    "BundleScorer",
    "LoadedBundle",
    "TwoTowerClassifier",
    "load_inference_bundle",
]
