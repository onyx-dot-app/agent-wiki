"""Relevance filtering for document ingestion.

Public interface: the :class:`RelevanceFilter` contract and the concrete
filters — :class:`CosineSimilarityFilter` (the cold-start model) and
:class:`TwoTowerFilter` (the warm, per-deployment model, which scores through a
pluggable :class:`Scorer`). Filters operate on the pipeline carriers
:class:`app.ingest.types.IngestionDocument` and
:class:`app.ingest.types.CandidatePage`, whose embeddings are filled by
``app.ingest.enrich``.

``TwoTowerScorer`` is intentionally NOT re-exported here — it's the wiring
behind :class:`TwoTowerFilter`, not part of the filter contract. Callers take a
:class:`RelevanceFilter` from ``build_relevance_filter``; only the factory (and
tests) construct a scorer directly, via ``app.ingest.relevance.two_tower_scorer``.
"""
from app.ingest.relevance.cosine_filter import CosineSimilarityFilter
from app.ingest.relevance.factory import build_relevance_filter
from app.ingest.relevance.filter import RelevanceFilter
from app.ingest.relevance.two_tower_filter import Scorer, TwoTowerFilter

__all__ = [
    "CosineSimilarityFilter",
    "RelevanceFilter",
    "Scorer",
    "TwoTowerFilter",
    "build_relevance_filter",
]
