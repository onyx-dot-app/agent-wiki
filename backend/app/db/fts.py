"""FTS5 (bm25) helpers for the wiki document index.

The actual document text lives in the git-backed wiki filesystem; this index
is a derived structure rebuilt on indexing tasks. Keep schema changes in
migrations under ``app/db/migrations/``.
"""
from __future__ import annotations

from app.db.sqlite import cursor


def upsert_document(doc_id: str, path: str, title: str, body: str) -> None:
    """Insert or replace a document in the FTS index."""
    with cursor() as cur:
        cur.execute("DELETE FROM documents_fts WHERE doc_id = ?", (doc_id,))
        cur.execute(
            "INSERT INTO documents_fts(doc_id, path, title, body) VALUES (?, ?, ?, ?)",
            (doc_id, path, title, body),
        )


def delete_document(doc_id: str) -> None:
    with cursor() as cur:
        cur.execute("DELETE FROM documents_fts WHERE doc_id = ?", (doc_id,))


def search(query: str, limit: int = 20) -> list[dict]:
    """Run a bm25 ranked search. ``query`` follows FTS5 query syntax.

    The ``snippet`` column is FTS5's match-aware extraction: it picks the
    densest cluster of matching terms in ``body`` and returns ~64 tokens of
    surrounding context, with each match wrapped in ``**...**`` so the
    LLM consumer sees standard markdown bold.
    """
    with cursor() as cur:
        rows = cur.execute(
            "SELECT doc_id, path, title, "
            "       snippet(documents_fts, 3, '**', '**', '…', 64) AS snippet, "
            "       bm25(documents_fts) AS score "
            "FROM documents_fts WHERE documents_fts MATCH ? "
            "ORDER BY score LIMIT ?",
            (query, limit),
        ).fetchall()
        return [dict(r) for r in rows]
