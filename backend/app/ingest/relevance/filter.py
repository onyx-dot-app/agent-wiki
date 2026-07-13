"""The relevance-filter contract."""
from __future__ import annotations

from abc import ABC, abstractmethod

from app.ingest.types import CandidatePage, IngestionDocument


class RelevanceFilter(ABC):
    """Decides whether a pushed document is relevant to a wiki page, so the
    expensive LLM reconcile only runs on plausible pairs.

    The contract is intentionally minimal — domain objects in, boolean out — so
    any kind of model fits: cosine similarity or a trained two-tower over
    embeddings, an LLM yes/no, or a metadata rule. How a verdict is reached
    (embedding, scoring, thresholding, prompting) is entirely a subclass
    concern; nothing here assumes a numeric score exists or that the inputs are
    vectors.

    Inputs are the pipeline carriers :class:`IngestionDocument` and
    :class:`CandidatePage`. A filter reads only what it needs — an embedding
    model reads their ``embedding`` slots (filled by an earlier enrichment
    stage), a rule reads ``metadata``, an LLM reads the text — so no field is
    required by the contract.
    """

    @abstractmethod
    def is_relevant(self, doc: IngestionDocument, page: CandidatePage) -> bool:
        """Whether ``doc`` is relevant to ``page``."""

    def keep_relevant(
        self, doc: IngestionDocument, pages: list[CandidatePage]
    ) -> list[CandidatePage]:
        """Return the subset of ``pages`` the document is relevant to.

        Defaults to a per-page loop. A model may override to batch — e.g. score
        the document's embedding against all candidate embeddings in one pass.
        """
        return [page for page in pages if self.is_relevant(doc, page)]
