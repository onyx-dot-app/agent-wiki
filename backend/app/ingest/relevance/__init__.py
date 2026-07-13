"""Relevance filtering for document ingestion.

Public interface: the :class:`RelevanceFilter` contract. It operates on the
pipeline carriers :class:`app.ingest.types.IngestionDocument` and
:class:`app.ingest.types.CandidatePage`. Concrete models (cosine, two-tower,
...) live in their own modules and subclass ``RelevanceFilter``; the pipeline
depends only on this interface.
"""
from app.ingest.relevance.filter import RelevanceFilter

__all__ = ["RelevanceFilter"]
