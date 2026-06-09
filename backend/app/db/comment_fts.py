"""BM25 full-text search for comments via OpenSearch.

A **separate** index from the document index (`wiki-docs`), one OpenSearch
document per comment.  Keeping comments in their own index is deliberate: the
document-ingestion candidate search (`app.db.fts.search`, field-scoped to
`title`/`body` over `wiki-docs`) and its `INGEST_BM25_MIN_SCORE` calibration
must stay untouched.  OpenSearch BM25 statistics are per-index, so a separate
index can't perturb `wiki-docs` scoring, and nothing here writes to `wiki-docs`.

The index name is derived from the doc index (`<opensearch_index>-comments`) so
the per-test isolation the conftest already sets up for `wiki-docs` extends to
comments for free.

Like `app.db.fts`, writes are best-effort: failures log at WARNING and return
without raising so a search-index glitch never aborts a comment mutation, and
search degrades to an empty list on connection errors.  Visibility is filtered
in Python by `doc_path` (comments inherit the page's read access).
"""
from __future__ import annotations

import logging

from opensearchpy import OpenSearch  # type: ignore[import-untyped]
from opensearchpy.exceptions import NotFoundError  # type: ignore[import-untyped]
from pydantic import BaseModel

from app.db import fts

log = logging.getLogger(__name__)

# Tracks which index name we've ensured a mapping for. When CONFIG changes
# (e.g. a new per-test index name), this differs from the current name and we
# re-ensure — so no explicit reset hook is needed between tests.
_ensured_index: str | None = None


class CommentDoc(BaseModel):
    comment_id: str
    doc_path: str
    thread_root_id: str
    body: str
    author_user_id: str | None
    mentioned_user_ids: list[str]
    status: str


class CommentSearchHit(BaseModel):
    comment_id: str
    doc_path: str
    thread_root_id: str
    snippet: str
    score: float


_MAPPING = {
    "settings": {
        "index": {
            "similarity": {"default": {"type": "BM25", "b": 0.75, "k1": 1.2}},
        },
    },
    "mappings": {
        "properties": {
            "comment_id": {"type": "keyword"},
            "doc_path": {"type": "keyword"},
            "thread_root_id": {"type": "keyword"},
            "body": {"type": "text"},
            "author_user_id": {"type": "keyword"},
            "mentioned_user_ids": {"type": "keyword"},
            "status": {"type": "keyword"},
        },
    },
}


def _index_name() -> str:
    from app.config import CONFIG

    return f"{CONFIG.opensearch_index}-comments"


def _client() -> object | None:
    """The shared OpenSearch client (created/owned by ``app.db.fts``), with our
    own index ensured once per index name."""
    global _ensured_index
    client = fts.get_client()  # reuse the singleton + its connection
    if client is None:
        return None
    idx = _index_name()
    if _ensured_index != idx:
        try:
            c: OpenSearch = client  # type: ignore[assignment]
            if not c.indices.exists(index=idx):
                c.indices.create(index=idx, body=_MAPPING)
                log.info("comment_fts: created index %s", idx)
            _ensured_index = idx
        except Exception:
            log.warning("comment_fts: failed to ensure index %s", idx, exc_info=True)
            return None
    return client


def drop_index_for_tests() -> None:
    """Delete the per-test comment index (mirrors ``fts.drop_index_for_tests``)."""
    global _ensured_index
    client = _client()
    _ensured_index = None
    if client is None:
        return
    try:
        c: OpenSearch = client  # type: ignore[assignment]
        try:
            c.indices.delete(index=_index_name())
        except NotFoundError:
            pass
    except Exception:
        log.warning("comment_fts: drop_index_for_tests failed", exc_info=True)


# --------------------------------------------------------------------------- #
# Write operations                                                             #
# --------------------------------------------------------------------------- #


def index_comment(doc: CommentDoc) -> None:
    client = _client()
    if client is None:
        return
    try:
        c: OpenSearch = client  # type: ignore[assignment]
        c.index(
            index=_index_name(),
            id=doc.comment_id,
            body=doc.model_dump(),
            refresh=True,  # type: ignore[call-arg]
        )
    except Exception:
        log.warning("comment_fts: index_comment failed for %s", doc.comment_id, exc_info=True)


def count() -> int | None:
    """Number of indexed comments, or None if the index is unavailable."""
    client = _client()
    if client is None:
        return None
    try:
        c: OpenSearch = client  # type: ignore[assignment]
        return int(c.count(index=_index_name())["count"])
    except Exception:
        log.warning("comment_fts: count failed", exc_info=True)
        return None


def delete_comment(comment_id: str) -> None:
    client = _client()
    if client is None:
        return
    try:
        c: OpenSearch = client  # type: ignore[assignment]
        try:
            c.delete(index=_index_name(), id=comment_id, refresh=True)  # type: ignore[call-arg]
        except NotFoundError:
            pass
    except Exception:
        log.warning("comment_fts: delete_comment failed for %s", comment_id, exc_info=True)


def _delete_by(field: str, value: str) -> None:
    client = _client()
    if client is None:
        return
    try:
        c: OpenSearch = client  # type: ignore[assignment]
        c.delete_by_query(
            index=_index_name(),
            body={"query": {"term": {field: value}}},
            refresh=True,  # type: ignore[call-arg]
            conflicts="proceed",  # type: ignore[call-arg]
        )
    except Exception:
        log.warning("comment_fts: delete_by %s=%s failed", field, value, exc_info=True)


def delete_thread(thread_root_id: str) -> None:
    """Remove every comment in a thread from the index (thread deleted or orphaned)."""
    _delete_by("thread_root_id", thread_root_id)


def delete_for_doc(doc_path: str) -> None:
    """Remove every comment on a page from the index (page deleted)."""
    _delete_by("doc_path", doc_path)


def reassign_doc_path(old_path: str, new_path: str) -> None:
    """Repoint a page's comment docs to a new path (page move/rename)."""
    client = _client()
    if client is None:
        return
    try:
        c: OpenSearch = client  # type: ignore[assignment]
        c.update_by_query(
            index=_index_name(),
            body={
                "query": {"term": {"doc_path": old_path}},
                "script": {
                    "source": "ctx._source.doc_path = params.p",
                    "params": {"p": new_path},
                },
            },
            refresh=True,  # type: ignore[call-arg]
            conflicts="proceed",  # type: ignore[call-arg]
        )
    except Exception:
        log.warning(
            "comment_fts: reassign_doc_path %s->%s failed", old_path, new_path, exc_info=True
        )


# --------------------------------------------------------------------------- #
# Search                                                                       #
# --------------------------------------------------------------------------- #


def search(
    query: str,
    limit: int = 20,
    *,
    user_id: str | None = None,
    is_admin: bool = False,
    apply_visibility: bool = True,
) -> list[CommentSearchHit]:
    client = _client()
    if client is None:
        return []

    fetch_size = min(limit * 5, 200)
    body = {
        "query": {
            "bool": {
                "must": {"match": {"body": query}},
                # Orphaned comments are removed from the index, but filter
                # defensively so a stale doc never surfaces.
                "filter": {"terms": {"status": ["open", "resolved"]}},
            },
        },
        "highlight": {"fields": {"body": {"fragment_size": 200, "number_of_fragments": 1}}},
        "_source": ["comment_id", "doc_path", "thread_root_id"],
        "size": fetch_size,
    }

    try:
        c: OpenSearch = client  # type: ignore[assignment]
        resp = c.search(index=_index_name(), body=body)
    except Exception:
        log.warning("comment_fts: search failed for query %r", query, exc_info=True)
        return []

    hits = resp.get("hits", {}).get("hits", [])
    if not hits:
        return []

    rows: list[tuple[str, str, str, str, float]] = []
    for h in hits:
        src = h.get("_source", {})
        doc_path: str = src.get("doc_path", "")
        if not doc_path:
            continue
        comment_id: str = src.get("comment_id", "") or h["_id"]
        thread_root_id: str = src.get("thread_root_id", "")
        hl: dict[str, list[str]] = h.get("highlight") or {}
        snippet_parts: list[str] = hl.get("body") or []
        rows.append(
            (
                comment_id,
                doc_path,
                thread_root_id,
                snippet_parts[0] if snippet_parts else "",
                float(h.get("_score", 0.0)),
            )
        )

    if apply_visibility and not is_admin and rows:
        from app.wiki import acl

        visible = set(acl.filter_paths_in_python(user_id, is_admin, {r[1] for r in rows}))
        rows = [r for r in rows if r[1] in visible]

    return [
        CommentSearchHit(
            comment_id=cid, doc_path=dp, thread_root_id=trid, snippet=snip, score=score
        )
        for cid, dp, trid, snip, score in rows[:limit]
    ]
