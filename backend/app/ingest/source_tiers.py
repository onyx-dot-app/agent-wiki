"""Source-tier classification for ingest.

Two tiers:
  normal   — full pipeline: BM25 search → score filter → updater LLM
  filtered — dropped silently before any processing

Add source types to ``FILTERED_SOURCES`` as needed. Empty in v0.
"""
from __future__ import annotations

FILTERED_SOURCES: frozenset[str] = frozenset()


def is_filtered(source_type: str | None) -> bool:
    if source_type is None:
        return False
    return source_type in FILTERED_SOURCES
