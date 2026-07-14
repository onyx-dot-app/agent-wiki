"""Postgres store for wiki page embeddings (ingestion relevance filter).

Durable storage only — Postgres never computes similarity (no pgvector). The
filter scores an incoming doc against *every* page, so scoring runs against an
in-worker matrix built from :func:`load_all`; this module just persists and
loads the raw vectors (packed float32 via ``app.llm.embeddings.pack``).

Callers (the reindex path) invoke these best-effort — a store glitch logs and
is swallowed there so it never aborts a doc commit.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import NamedTuple

from sqlalchemy import delete as sa_delete, select

from app.db.models import PageEmbedding
from app.db.session import session


class PageVector(NamedTuple):
    """A page's stored embedding: its wiki path and the packed float32 vector
    (unpack with ``app.llm.embeddings.unpack``). The unit :func:`load_all`
    returns to build the in-worker scoring matrix."""

    path: str
    vector: bytes


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def get_sha(path: str) -> str | None:
    """Stored content hash for ``path`` (or ``None`` if not stored) — the
    re-embed guard: skip re-embedding a page whose body is unchanged."""
    with session() as s:
        row = s.get(PageEmbedding, path)
        return row.content_sha256 if row else None


def get_vector(path: str) -> bytes | None:
    """Stored packed vector for ``path`` (unpack with
    ``app.llm.embeddings.unpack``), or ``None`` if the page has no embedding."""
    with session() as s:
        row = s.get(PageEmbedding, path)
        return bytes(row.vector) if row else None


def load_paths(paths: list[str]) -> dict[str, bytes]:
    """``path -> packed vector`` for the given paths that have a stored
    embedding (missing paths are simply absent). One query — used to attach
    candidate-page vectors before relevance filtering."""
    if not paths:
        return {}
    with session() as s:
        return {
            path: bytes(vec)
            for path, vec in s.execute(
                select(PageEmbedding.path, PageEmbedding.vector).where(
                    PageEmbedding.path.in_(paths)
                )
            )
        }


def all_shas() -> dict[str, str]:
    """``path -> content_sha256`` for every stored page. Cheap (no vectors);
    used by backfill / reconcile to find missing or stale pages."""
    with session() as s:
        return {
            path: sha
            for path, sha in s.execute(
                select(PageEmbedding.path, PageEmbedding.content_sha256)
            )
        }


def upsert(path: str, content_sha256: str, model: str, vector: bytes) -> None:
    with session() as s:
        row = s.get(PageEmbedding, path)
        if row is None:
            row = PageEmbedding(path=path)
            s.add(row)
        row.content_sha256 = content_sha256
        row.model = model
        row.vector = vector
        row.updated_at = _now()


def delete(path: str) -> None:
    with session() as s:
        s.execute(sa_delete(PageEmbedding).where(PageEmbedding.path == path))


def load_all(model: str | None = None) -> list[PageVector]:
    """All stored page vectors, optionally filtered to a single embedding
    ``model``. Builds the in-worker scoring matrix — a cold load / periodic
    refresh, never the per-document hot path."""
    with session() as s:
        stmt = select(PageEmbedding.path, PageEmbedding.vector)
        if model:
            stmt = stmt.where(PageEmbedding.model == model)
        return [PageVector(path, bytes(vec)) for path, vec in s.execute(stmt)]
