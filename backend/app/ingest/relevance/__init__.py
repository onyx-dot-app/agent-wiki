"""Relevance filtering for document ingestion.

Public interface: the :class:`RelevanceFilter` contract, plus the concrete
:class:`CosineSimilarityFilter` (the cold-start model). Filters operate on the
pipeline carriers :class:`app.ingest.types.IngestionDocument` and
:class:`app.ingest.types.CandidatePage`, whose embeddings are filled by
``app.ingest.enrich``.
"""
from app.ingest.relevance.cosine_filter import CosineSimilarityFilter
from app.ingest.relevance.filter import RelevanceFilter

__all__ = ["CosineSimilarityFilter", "RelevanceFilter"]
