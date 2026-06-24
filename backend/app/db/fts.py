"""BM25 full-text search via OpenSearch.

Index name: ``wiki-docs``.  One document per wiki page; ``_id`` = path
(e.g. ``"docs/spec.md"``).  Stored fields: ``path`` (keyword), ``title``
and ``body`` (text).

Client is lazily initialised on first use so import-time startup
(migrations, config load) never touches the network.  A module-level
singleton is reused for the lifetime of the process; call
``reset_client_for_tests()`` between tests that reconfigure CONFIG.

Writes are best-effort: a failed upsert/delete logs at WARNING and
returns without raising so a search-index glitch never aborts a doc
commit.  Search degrades to an empty list on connection errors.

Visibility filtering: uses ``acl.filter_paths_in_python`` so the check
works regardless of whether the ``documents`` table has rows (the table
is not populated by the current write path).
"""
from __future__ import annotations

import logging
import threading

from opensearchpy import OpenSearch  # type: ignore[import-untyped]
from opensearchpy.exceptions import NotFoundError  # type: ignore[import-untyped]
from pydantic import BaseModel

log = logging.getLogger(__name__)

_client_lock = threading.Lock()
_client: object | None = None
_client_ready = False  # True once we've attempted init (even if it failed)

class SearchHit(BaseModel):
    doc_id: str
    path: str
    title: str | None
    snippet: str
    score: float

# --------------------------------------------------------------------------- #
# Client lifecycle                                                             #
# --------------------------------------------------------------------------- #

def _make_client(url: str) -> object:
    import re

    # urlparse mishandles passwords with special chars (e.g. '?') — use regex instead.
    m = re.match(
        r"(?P<scheme>https?)://"
        r"(?:(?P<user>[^:@]+):(?P<password>.+)@)?"
        r"(?P<host>[^:@/?]+)"
        r"(?::(?P<port>\d+))?",
        url,
    )
    if not m:
        raise ValueError(f"Cannot parse OpenSearch URL: {url!r}")
    use_ssl = m.group("scheme") == "https"
    port = int(m.group("port")) if m.group("port") else (443 if use_ssl else 9200)
    host = {"host": m.group("host"), "port": port}

    kwargs: dict[str, object] = {
        "hosts": [host],
        "use_ssl": use_ssl,
        "verify_certs": use_ssl,
        "ssl_show_warn": False,
        "http_compress": True,
    }
    if m.group("user"):
        kwargs["http_auth"] = (m.group("user"), m.group("password") or "")

    return OpenSearch(**kwargs)  # type: ignore[arg-type]

def _get_client() -> object | None:
    global _client, _client_ready
    if _client_ready:
        return _client
    with _client_lock:
        if _client_ready:
            return _client
        from app.config import CONFIG

        url = CONFIG.opensearch_url
        if not url:
            _client_ready = True
            return None
        try:
            _client = _make_client(url)
            _ensure_index(_client)
        except Exception:
            log.exception("fts: failed to initialise OpenSearch client")
            _client = None
        _client_ready = True
    return _client

def get_client() -> object | None:
    """Public accessor for the shared OpenSearch client (used by sibling
    index modules like ``comment_fts`` so they share one connection)."""
    return _get_client()


def reset_client_for_tests() -> None:
    """Drop the cached client.  Call between tests that change CONFIG."""
    global _client, _client_ready
    with _client_lock:
        _client = None
        _client_ready = False

def drop_index_for_tests() -> None:
    """Delete the entire per-test index.  Each test gets a unique index name
    (set in conftest via ``opensearch_index`` in Config) so deleting it is
    safe and leaves no residue in the shared OpenSearch instance."""
    client = _get_client()
    if client is None:
        return
    try:

        c: OpenSearch = client  # type: ignore[assignment]
        try:
            c.indices.delete(index=_index_name())
        except NotFoundError:
            pass
    except Exception:
        log.warning("fts: drop_index_for_tests failed", exc_info=True)

# --------------------------------------------------------------------------- #
# Index bootstrap                                                              #
# --------------------------------------------------------------------------- #

_MAPPING = {
    "settings": {
        "index": {
            "similarity": {"default": {"type": "BM25", "b": 0.75, "k1": 1.2}},
        },
    },
    "mappings": {
        "properties": {
            "path":  {"type": "keyword"},
            "title": {"type": "text", "boost": 3.0},
            "body":  {"type": "text"},
        },
    },
}

def _index_name() -> str:
    from app.config import CONFIG
    return CONFIG.opensearch_index

def _ensure_index(client: object) -> None:

    c: OpenSearch = client  # type: ignore[assignment]
    idx = _index_name()
    if not c.indices.exists(index=idx):
        c.indices.create(index=idx, body=_MAPPING)
        log.info("fts: created index %s", idx)

# --------------------------------------------------------------------------- #
# Write operations                                                             #
# --------------------------------------------------------------------------- #

def upsert_document(
    doc_id: str,
    path: str,
    title: str,
    body: str,
    *,
    indexed_sha: str | None = None,
) -> None:
    """Index (or re-index) a wiki page.

    ``doc_id`` is accepted for API compatibility but ``path`` is used as
    the OpenSearch ``_id`` so deletes (which always pass the path) stay
    consistent.
    """
    client = _get_client()
    if client is None:
        return
    try:

        c: OpenSearch = client  # type: ignore[assignment]
        c.index(
            index=_index_name(),
            id=path,  # stable _id regardless of Postgres UUID
            body={"path": path, "title": title, "body": body},
            refresh=True,  # type: ignore[call-arg]
        )
    except Exception:
        log.warning("fts: upsert_document failed for %s", path, exc_info=True)

def count_documents() -> int | None:
    """Return the total number of indexed wiki pages, or None if unavailable."""
    client = _get_client()
    if client is None:
        return None
    try:

        c: OpenSearch = client  # type: ignore[assignment]
        result = c.count(index=_index_name())
        return int(result["count"])
    except Exception:
        log.warning("fts: count_documents failed", exc_info=True)
        return None


def paths_under(prefix: str) -> list[str]:
    """Indexed page paths under a folder ``prefix`` (e.g. ``"team/"``).

    ``path`` is a keyword field, so a prefix term matches the stored value.
    ``prefix=""`` matches every page. Bounded to the first 10k matches (the
    realistic ceiling).

    Returns ``[]`` only when OpenSearch isn't configured. A backend error
    (e.g. the search endpoint is down while ``count`` still answers) **raises**
    rather than returning ``[]`` — an empty result must mean "genuinely no
    pages here", so callers can't mistake a partial outage for an empty folder.
    """
    client = _get_client()
    if client is None:
        return []
    c: OpenSearch = client  # type: ignore[assignment]
    resp = c.search(
        index=_index_name(),
        body={
            "size": 10_000,
            "query": {"prefix": {"path": prefix}},
            "_source": ["path"],
        },
    )
    return [h["_source"]["path"] for h in resp["hits"]["hits"]]

def delete_document(doc_id: str) -> None:
    """Remove a page from the index.  ``doc_id`` is the path when called
    from ``app.wiki.notify`` (the only callers)."""
    client = _get_client()
    if client is None:
        return
    try:

        c: OpenSearch = client  # type: ignore[assignment]
        try:
            c.delete(index=_index_name(), id=doc_id)
        except NotFoundError:
            pass
    except Exception:
        log.warning("fts: delete_document failed for %s", doc_id, exc_info=True)

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
    raise_on_error: bool = False,
) -> list[SearchHit]:
    client = _get_client()
    if client is None:
        return []

    # Over-fetch 5x (capped) when visibility filtering may drop hits, so we
    # still have ``limit`` survivors. With no visibility filter (e.g. ingest),
    # fetch exactly ``limit`` — this lets ingest request a wide candidate set
    # without the 200-doc cap silently truncating it.
    fetch_size = limit if not apply_visibility else min(limit * 5, 200)
    body = {
        "query": {
            "multi_match": {
                "query": query,
                "fields": ["title^3", "body"],
                "type": "best_fields",
            },
        },
        "highlight": {
            "fields": {
                "body":  {"fragment_size": 200, "number_of_fragments": 1},
                "title": {"number_of_fragments": 0},
            },
        },
        "_source": ["path", "title"],
        "size": fetch_size,
    }

    try:

        c: OpenSearch = client  # type: ignore[assignment]
        resp = c.search(index=_index_name(), body=body)
    except Exception:
        # With raise_on_error, propagate so the caller can distinguish a backend
        # failure (e.g. OpenSearch rejecting an oversized query) from a genuine
        # no-match. Otherwise log and return [] — the search surface degrades to
        # empty results.
        if raise_on_error:
            raise
        log.warning("fts: search failed for query %r", query, exc_info=True)
        return []

    hits = resp.get("hits", {}).get("hits", [])
    if not hits:
        return []

    # Build (path, title, snippet, score) tuples.
    candidates: list[tuple[str, str | None, str, float]] = []
    for h in hits:
        src = h.get("_source", {})
        path: str = src.get("path", "") or h["_id"]
        title: str | None = src.get("title") or None
        hl: dict[str, list[str]] = h.get("highlight") or {}
        snippet_parts: list[str] = hl.get("body") or hl.get("title") or []
        snippet: str = snippet_parts[0] if snippet_parts else ""
        candidates.append((path, title, snippet, float(h.get("_score", 0.0))))

    if apply_visibility and candidates:
        visible = _visible_paths(
            {c[0] for c in candidates}, user_id=user_id, is_admin=is_admin
        )
        candidates = [c for c in candidates if c[0] in visible]

    return [
        SearchHit(doc_id=path, path=path, title=title, snippet=snippet, score=score)
        for path, title, snippet, score in candidates[:limit]
    ]

def _visible_paths(
    paths: set[str],
    *,
    user_id: str | None,
    is_admin: bool,
) -> set[str]:
    """Filter ``paths`` to those the caller can read.

    Uses ``acl.filter_paths_in_python`` so this works without Document
    table rows (the current write path never populates that table).
    """
    if is_admin or not paths:
        return paths
    from app.wiki import acl

    return set(acl.filter_paths_in_python(user_id, is_admin, paths))
