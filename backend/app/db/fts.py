"""BM25 search stub — returns empty results until OpenSearch lands.

pg_textsearch has been removed (not available on RDS). Real search will
be restored in the follow-up PR that wires up self-hosted OpenSearch.
The public API is preserved so callers don't need changes.
"""
from __future__ import annotations

from pydantic import BaseModel


class SearchHit(BaseModel):
    doc_id: str
    path: str
    title: str | None
    snippet: str
    score: float


def upsert_document(
    doc_id: str,
    path: str,
    title: str,
    body: str,
    *,
    indexed_sha: str | None = None,
) -> None:
    pass


def delete_document(doc_id: str) -> None:
    pass


def search(
    query: str,
    limit: int = 20,
    *,
    user_id: str | None = None,
    is_admin: bool = False,
    apply_visibility: bool = True,
) -> list[SearchHit]:
    return []
