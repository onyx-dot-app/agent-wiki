"""Domain models for the ingest pipeline."""
from __future__ import annotations

from pydantic import BaseModel


class WikiUpdateCandidate(BaseModel):
    # Wiki-relative path of the candidate page.
    path: str
    # The relevance filter's score for the (document, page) pair; None when the
    # pair was kept fail-open (unscorable — e.g. a missing embedding).
    score: float | None
    body: str
    # Resolved per-page update instruction (most-granular scope wins), rendered
    # into the reconciler prompt. ``None`` when no policy applies.
    update_instruction: str | None = None
