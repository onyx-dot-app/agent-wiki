"""Domain models for the ingest pipeline."""
from __future__ import annotations

from pydantic import BaseModel

from app.db.fts import SearchHit


class WikiUpdateCandidate(BaseModel):
    hit: SearchHit
    body: str
    # Resolved per-page update instruction (most-granular scope wins), rendered
    # into the reconciler prompt. ``None`` when no policy applies.
    update_instruction: str | None = None
