"""Enrichment stages: attach derived data to the ingestion carriers.

The relevance filter compares embeddings, but the raw carriers arrive without
them — an :class:`IngestionDocument` from the inbound push, a
:class:`CandidatePage` from search. These helpers fill the ``embedding`` slots:
the document is embedded once; each page's vector is read from the stored
page-embedding table by path.

Best-effort, like the rest of the embedding path: if a vector can't be produced
(no OpenAI key, an embed error, or a page not yet embedded) the carrier is
returned with its ``embedding`` left ``None``. Downstream filters treat ``None``
as "unavailable" and stay fail-open.
"""
from __future__ import annotations

from dataclasses import replace

from app.db import page_embeddings
from app.ingest.types import CandidatePage, IngestionDocument
from app.llm import embeddings


def with_document_embedding(doc: IngestionDocument) -> IngestionDocument:
    """Return ``doc`` with its ``embedding`` filled (best-effort).

    Embeds the capped content once. A no-op if already embedded, or if embedding
    is unavailable (returns the document with ``embedding`` still ``None``).
    """
    if doc.embedding is not None:
        return doc
    vec = embeddings.embed_text(doc.content[: embeddings.DOC_CHAR_CAP])
    return doc if vec is None else replace(doc, embedding=vec)


def with_page_embeddings(pages: list[CandidatePage]) -> list[CandidatePage]:
    """Return ``pages`` with each ``embedding`` filled from the store by path.

    One batched load. A page whose vector isn't already set and isn't stored
    keeps ``embedding=None``.
    """
    if not pages:
        return []
    to_load = [p.path for p in pages if p.embedding is None]
    blobs = page_embeddings.load_paths(to_load) if to_load else {}
    out: list[CandidatePage] = []
    for page in pages:
        if page.embedding is not None:
            out.append(page)
            continue
        blob = blobs.get(page.path)
        out.append(page if blob is None else replace(page, embedding=embeddings.unpack(blob)))
    return out
