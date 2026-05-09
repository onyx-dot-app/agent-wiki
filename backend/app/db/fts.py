"""BM25 helpers for the wiki document index, backed by pg_textsearch.

The actual document text lives in the git-backed wiki filesystem; this index
is a derived structure rebuilt on indexing tasks. Schema for ``documents_fts``
lives in ``app.db.models``; the pg_textsearch BM25 access method is registered
by the extension and the index DDL is part of ``DocumentFts.__table_args__``.

pg_textsearch's ``<@>`` operator and ``to_bm25query()`` function don't map
cleanly onto SQLAlchemy expressions, so the search query goes through
``text()``. Snippets are synthesized in Python because pg_textsearch has no
``snippet()`` analogue.
"""
from __future__ import annotations

import re

from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.db.models import DocumentFts
from app.db.session import session


class SearchHit(BaseModel):
    """One ranked BM25 result. ``score`` is the un-negated pg_textsearch
    score (higher is more relevant)."""

    doc_id: str
    path: str
    title: str | None
    snippet: str
    score: float

_SNIPPET_RADIUS = 64        # tokens of context around the densest match cluster
_SNIPPET_ELLIPSIS = "…"
_TOKEN_RE = re.compile(r"\w+", re.UNICODE)


def upsert_document(doc_id: str, path: str, title: str, body: str) -> None:
    """Insert or replace a document in the BM25 index."""
    stmt = pg_insert(DocumentFts).values(doc_id=doc_id, path=path, title=title, body=body)
    stmt = stmt.on_conflict_do_update(
        index_elements=[DocumentFts.doc_id],
        set_={"path": stmt.excluded.path, "title": stmt.excluded.title, "body": stmt.excluded.body},
    )
    with session() as s:
        s.execute(stmt)


def delete_document(doc_id: str) -> None:
    with session() as s:
        row = s.get(DocumentFts, doc_id)
        if row is not None:
            s.delete(row)


_SEARCH_SQL = text(
    "SELECT doc_id, path, title, body, "
    "       (coalesce(title, '') || ' ' || coalesce(body, '')) <@> "
    "           to_bm25query(:q, 'documents_fts_bm25') AS neg_score "
    "FROM documents_fts "
    "ORDER BY (coalesce(title, '') || ' ' || coalesce(body, '')) <@> "
    "           to_bm25query(:q, 'documents_fts_bm25') ASC "
    "LIMIT :lim"
)


def search(query: str, limit: int = 20) -> list[SearchHit]:
    """Run a BM25 ranked search.

    Ranking is from pg_textsearch (``<@> to_bm25query(...)`` returns a
    negated score for ASC ordering). The ``snippet`` is synthesized in
    Python: we find the densest cluster of query terms in ``body`` and
    return ~``_SNIPPET_RADIUS`` tokens of surrounding context, with each
    match wrapped in ``**...**`` so the LLM consumer sees standard
    markdown bold.
    """
    with session() as s:
        rows = s.execute(_SEARCH_SQL, {"q": query, "lim": limit}).mappings().all()

    terms = _query_terms(query)
    out: list[SearchHit] = []
    for r in rows:
        neg_score = r["neg_score"] or 0.0
        # pg_textsearch returns 0 for non-matches; only keep real hits.
        if neg_score >= 0:
            continue
        out.append(
            SearchHit(
                doc_id=r["doc_id"],
                path=r["path"],
                title=r["title"],
                snippet=_make_snippet(r["body"] or "", terms),
                score=-neg_score,
            )
        )
    return out


def _query_terms(query: str) -> list[str]:
    """Extract searchable terms from a free-form query (lowercase, alnum tokens)."""
    return [m.group(0).lower() for m in _TOKEN_RE.finditer(query)]


def _make_snippet(body: str, terms: list[str]) -> str:
    """Pick a window of ~_SNIPPET_RADIUS tokens around the densest match cluster.

    Falls back to the document head if no terms hit. Matches are wrapped in
    ``**...**`` for markdown bold.
    """
    if not body:
        return ""
    tokens: list[tuple[int, int, str]] = [
        (m.start(), m.end(), m.group(0)) for m in _TOKEN_RE.finditer(body)
    ]
    if not tokens:
        return body[:512]

    term_set = set(terms)
    hit_indices = [i for i, (_, _, t) in enumerate(tokens) if t.lower() in term_set]

    if not hit_indices:
        end_token = min(len(tokens) - 1, _SNIPPET_RADIUS)
        snippet_text = body[: tokens[end_token][1]]
        suffix = _SNIPPET_ELLIPSIS if end_token < len(tokens) - 1 else ""
        return snippet_text + suffix

    center = _densest_cluster_center(hit_indices, _SNIPPET_RADIUS)
    start_idx = max(0, center - _SNIPPET_RADIUS // 2)
    end_idx = min(len(tokens) - 1, start_idx + _SNIPPET_RADIUS)
    start_char = tokens[start_idx][0]

    pieces: list[str] = []
    cursor_pos = start_char
    for i in range(start_idx, end_idx + 1):
        tok_start, tok_end, tok_text = tokens[i]
        pieces.append(body[cursor_pos:tok_start])
        if tok_text.lower() in term_set:
            pieces.append(f"**{tok_text}**")
        else:
            pieces.append(tok_text)
        cursor_pos = tok_end
    snippet = "".join(pieces)
    prefix = _SNIPPET_ELLIPSIS if start_idx > 0 else ""
    suffix = _SNIPPET_ELLIPSIS if end_idx < len(tokens) - 1 else ""
    return prefix + snippet + suffix


def _densest_cluster_center(hit_indices: list[int], window: int) -> int:
    """Return the token index that anchors the densest hit cluster."""
    best_count = 0
    best_center = hit_indices[0]
    left = 0
    for right in range(len(hit_indices)):
        while hit_indices[right] - hit_indices[left] > window:
            left += 1
        count = right - left + 1
        if count > best_count:
            best_count = count
            best_center = hit_indices[(left + right) // 2]
    return best_center
