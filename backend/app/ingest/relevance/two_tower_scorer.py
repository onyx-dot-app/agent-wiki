"""The two-tower :class:`Scorer` — runs the exported two-tower ONNX graph.

Maps the filter's one-document-against-many-pages call onto the graph's
``(wiki, doc) -> prob`` contract: the page is the wiki side, the document is
tiled across the candidate pages, one forward pass returns P(update) per page.
Execution is delegated to :class:`OnnxModel`; this class owns only the
two-tower-specific I/O — the parallel to ``cosine_similarity_score`` for the
cosine filter.

The trained model is exported to ONNX offline (torch lives only in that export
step); the backend only ever loads and runs the resulting graph, in-process.
"""
from __future__ import annotations

from collections.abc import Sequence

import numpy as np

from app.ingest.relevance.onnx_model import OnnxModel

# Graph input/output names — must match what the ONNX export writes.
_WIKI_INPUT = "wiki"
_DOC_INPUT = "doc"
_PROB_OUTPUT = "prob"


class TwoTowerScorer:
    """A :class:`Scorer` backed by the two-tower ONNX graph, run in-process."""

    def __init__(self, model_path: str) -> None:
        self._model = OnnxModel(model_path)

    @property
    def cutoff(self) -> float | None:
        """The model's calibrated P(update) threshold, embedded at export time
        (``None`` if the graph carries no cutoff)."""
        raw = self._model.metadata().get("cutoff")
        return float(raw) if raw else None

    def score_batch(
        self, doc_vec: Sequence[float], page_vecs: list[Sequence[float]]
    ) -> list[float]:
        if not page_vecs:
            return []
        wiki = np.asarray(page_vecs, dtype=np.float32)
        # The document is scored against every page, so tile it across the batch.
        doc = np.asarray([list(doc_vec)] * len(page_vecs), dtype=np.float32)
        probs = self._model.run({_WIKI_INPUT: wiki, _DOC_INPUT: doc}, _PROB_OUTPUT)
        return probs.astype(np.float64).ravel().tolist()
