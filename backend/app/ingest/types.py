"""Domain types that flow through the ingestion pipeline.

A document pushed from an external system is normalized into an
:class:`IngestionDocument` at the API boundary (mapped from
``app.models.file_system.IngestRequest``) and then flows through the pipeline.
Stages *enrich* it — e.g. an embedding stage attaches ``embedding`` — and
downstream filters read whichever fields they need. :class:`CandidatePage` is
the wiki-side counterpart: a page the document might be relevant to.

Both are frozen. Enrichment produces a new copy via ``dataclasses.replace`` so
data flow through the pipeline stays explicit and stage order can't silently
mutate a shared object:

    doc = dataclasses.replace(doc, embedding=vec)

Derived fields default to ``None`` and mean "not computed yet"; readers treat
``None`` as "unavailable" and fall back (a relevance filter, for instance,
stays fail-open). Keep this carrier disciplined — raw inputs plus a few
well-defined derived slots, not a dumping ground.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class IngestionDocument:
    """A document being ingested, as it flows through the pipeline.

    Raw fields come from the inbound push; ``id`` is the source system's
    ``source_document_id``. ``embedding`` is a derived slot filled by an
    enrichment stage — ``None`` until then.
    """

    content: str
    id: str | None = None
    title: str | None = None
    source_type: str | None = None
    url: str | None = None
    metadata: dict[str, Any] | None = None
    embedding: list[float] | None = None


@dataclass(frozen=True)
class CandidatePage:
    """A wiki page the document might be relevant to.

    ``path`` is the wiki-relative path — also the key into the stored page
    embeddings. ``embedding`` is the page's vector, filled from that store by a
    pre-load stage; ``None`` until then.
    """

    path: str
    body: str
    title: str | None = None
    embedding: list[float] | None = None
