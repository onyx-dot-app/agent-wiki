"""Relevance filtering for document ingestion.

Public interface: the :class:`RelevanceFilter` contract and the concrete
filters — :class:`CosineSimilarityFilter` (the cold-start model) and
:class:`TwoTowerFilter` (the warm, per-deployment model, which scores through a
pluggable :class:`Scorer`). Filters operate on the pipeline carriers
:class:`app.ingest.types.IngestionDocument` and
:class:`app.ingest.types.CandidatePage`, whose embeddings are filled by
``app.ingest.enrich``.
"""
from app.ingest.relevance.cosine_filter import CosineSimilarityFilter
from app.ingest.relevance.filter import RelevanceFilter
from app.ingest.relevance.two_tower_filter import Scorer, TwoTowerFilter

__all__ = ["CosineSimilarityFilter", "RelevanceFilter", "Scorer", "TwoTowerFilter"]
