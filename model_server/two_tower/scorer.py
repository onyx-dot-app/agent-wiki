"""Score (document, page) embedding pairs with a trained two-tower bundle.

Maps the relevance-filter contract — one document vector against many
candidate-page vectors — onto the two-tower's ``(wiki, doc)`` inputs: the wiki
page is the ``wiki`` side, the incoming document is the ``doc`` side. Returns
P(update) == P(relevant) per page from a single forward pass.

The heavy work (loading the bundle, rebuilding the network) happens once via
:meth:`load`; scoring is then a pure forward pass on the provided vectors — no
embedding, no I/O.
"""
from __future__ import annotations

from pathlib import Path

import torch

from two_tower.bundle import LoadedBundle, load_inference_bundle

# Two-tower output classes: index 1 == "update" (i.e. relevant).
_UPDATE_CLASS_INDEX = 1


class BundleScorer:
    def __init__(self, bundle: LoadedBundle) -> None:
        self._bundle = bundle

    @classmethod
    def load(cls, path: str | Path, *, map_location: str = "cpu") -> "BundleScorer":
        return cls(load_inference_bundle(Path(path), map_location=map_location))

    @property
    def cutoff(self) -> float | None:
        """The bundle's default P(update) decision threshold (may be ``None``)."""
        return self._bundle.cutoff

    def score_batch(
        self, doc_vec: list[float], page_vecs: list[list[float]]
    ) -> list[float]:
        """P(relevant) for the document against each page, one per ``page_vecs``.

        The page is the ``wiki`` side; the document vector is tiled across all
        pages as the ``doc`` side. Returns ``[]`` for no pages.
        """
        if not page_vecs:
            return []
        wiki = torch.tensor(page_vecs, dtype=torch.float32)
        doc = torch.tensor([doc_vec] * len(page_vecs), dtype=torch.float32)
        with torch.no_grad():
            logits = self._bundle.model(wiki, doc)
            probs = torch.softmax(logits, dim=-1)[:, _UPDATE_CLASS_INDEX]
        return probs.tolist()
