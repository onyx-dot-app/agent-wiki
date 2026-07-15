"""Relevance filtering for document ingestion.

Public interface: the :class:`RelevanceFilter` contract and the concrete
filters — :class:`CosineSimilarityFilter` (the cold-start model) and
:class:`TwoTowerFilter` (the warm, per-deployment model, which scores through a
pluggable :class:`Scorer`). Filters operate on the pipeline carriers
:class:`app.ingest.types.IngestionDocument` and
:class:`app.ingest.types.CandidatePage`, whose embeddings are filled by
``app.ingest.enrich``.

``TwoTowerScorer`` is intentionally NOT re-exported here — it pulls in
``onnxruntime`` (via ``onnx_model``), and importing this package shouldn't cost
that shared-library load for callers that only want the filter contract or the
cosine filter. Import it directly from ``app.ingest.relevance.two_tower_scorer``
where the warm model is actually constructed (the ingest worker).
"""
from app.ingest.relevance.cosine_filter import CosineSimilarityFilter
from app.ingest.relevance.filter import RelevanceFilter
from app.ingest.relevance.two_tower_filter import Scorer, TwoTowerFilter

__all__ = [
    "CosineSimilarityFilter",
    "RelevanceFilter",
    "Scorer",
    "TwoTowerFilter",
]
