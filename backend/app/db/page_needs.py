"""Postgres store for per-page information needs.

Current-valued: a page's needs describe that page as it is now, so a re-extraction replaces
them. That is the opposite of ``app.db.entity_taxonomy``, which is append-only — and the
difference is that nothing keys facts by a need, so there is nothing a replacement can orphan.

The reason this is stored at all rather than recomputed is cost. Extraction is one LLM call per
page, so a corpus-wide re-run over an unchanged wiki is pure waste. :func:`stale_paths` is what
makes a re-run cost one call per edited page instead.

That same cost is why rows are keyed by ``doc_id`` rather than path: ``wiki_doc_ids`` re-keys
its path in place on a move, so a renamed page keeps its needs. Path-keyed, a rename would look
like a new page (buy its needs again) plus a vanished one (prune the old row) — paying twice for
a reorganization that changed no content.

Callers work in paths, because that is what reading the wiki yields; the id is resolved here.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any, NamedTuple

from sqlalchemy import delete as sa_delete, select

from app.db.models import PageNeeds, WikiDocId
from app.db.session import session
from app.wiki import doc_ids


class StoredNeeds(NamedTuple):
    """One page's stored needs, with the page's CURRENT path resolved from ``wiki_doc_ids``.

    A detached record, not the ORM row: repos return plain data so the rest of the app does not
    depend on SQLAlchemy, and so a read cannot become a lazy load against a closed session. Same
    shape of boundary as ``page_embeddings.PageVector``.

    The path is joined rather than stored, so it follows a move instead of going stale — see
    ``PageNeeds`` for why no path column exists.
    """

    doc_id: str
    path: str
    needs: list[dict[str, Any]]
    model: str
    taxonomy_id: int | None
    updated_at: str


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def content_sha256(body: str) -> str:
    """Hash of a page body — the re-extract guard. Uncapped, unlike the embedding store's:
    extraction sends the page whole, so a change past any cap still changes the result."""
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def stale_paths(
    pages: list[tuple[str, str]], *, model: str | None = None, taxonomy_id: int | None = None
) -> list[str]:
    """Which of ``pages`` need extracting: never extracted, edited since, or extracted under a
    different model or taxonomy. Returns paths, since that is what the caller holds bodies by.

    All three inputs are compared, not just the body, because each changes the output. A page
    skipped after a taxonomy re-derivation would keep entity types the current taxonomy no
    longer defines — stale in a way no later step could detect.

    A page with no minted id yet is stale by definition: nothing can have been stored for it.
    Two queries for the whole corpus regardless of size; the comparison is in Python because
    the caller already holds the bodies.
    """
    if not pages:
        return []
    want_model = model or ""
    paths = [path for path, _ in pages]
    ids = doc_ids.ids_for_paths(paths)
    with session() as s:
        stored = {
            row_doc_id: (sha, row_model, row_taxonomy)
            for row_doc_id, sha, row_model, row_taxonomy in s.execute(
                select(
                    PageNeeds.doc_id,
                    PageNeeds.content_sha256,
                    PageNeeds.model,
                    PageNeeds.taxonomy_id,
                )
            )
        }
    return [
        path
        for path, body in pages
        if path not in ids
        or stored.get(ids[path]) != (content_sha256(body), want_model, taxonomy_id)
    ]


def store(
    path: str,
    *,
    body: str,
    needs: list[dict[str, Any]],
    model: str | None = None,
    taxonomy_id: int | None = None,
) -> str:
    """Replace the needs of the page at ``path``. Returns the ``doc_id`` written.

    Mints an id if the page has none — extraction is a read, and minting lazily on read is how
    ``wiki_doc_ids`` backfills pre-existing content.

    An empty ``needs`` list is stored, not skipped: "this page tracks nothing durable" is a
    real answer, and recording it is what stops the page being re-extracted on every run.
    """
    doc_id = doc_ids.get_or_mint(path)
    with session() as s:
        row = s.get(PageNeeds, doc_id)
        if row is None:
            row = PageNeeds(doc_id=doc_id)
            s.add(row)
        row.content_sha256 = content_sha256(body)
        row.model = model or ""
        row.taxonomy_id = taxonomy_id
        row.needs = needs
        row.updated_at = _now()
    return doc_id


def _select_stored():
    """Shared projection behind every read — the ORM row never leaves this module."""
    return select(
        PageNeeds.doc_id,
        WikiDocId.path,
        PageNeeds.needs,
        PageNeeds.model,
        PageNeeds.taxonomy_id,
        PageNeeds.updated_at,
    ).join(WikiDocId, WikiDocId.id == PageNeeds.doc_id)


def get(path: str) -> StoredNeeds | None:
    """Stored needs for the live page at ``path``, or None if it has never been extracted.

    A trashed page resolves to None: ``id_for_path`` only matches live rows, so needs stop being
    reachable by path the moment the page is trashed rather than at the next extraction.
    """
    doc_id = doc_ids.id_for_path(path)
    return get_by_doc_id(doc_id) if doc_id else None


def get_by_doc_id(doc_id: str) -> StoredNeeds | None:
    """Stored needs by stable id — how an outside reference reads them back after the page has
    been renamed, which is the reason for keying this way.

    Unlike :func:`get`, this answers for a trashed page too: the caller asked for a specific
    document, so its own liveness check is the honest place for that decision.
    """
    with session() as s:
        row = s.execute(_select_stored().where(PageNeeds.doc_id == doc_id)).first()
    return StoredNeeds(*row) if row else None


def load_all() -> list[StoredNeeds]:
    """Every LIVE page's stored needs with its current path, path-ordered.

    What the corpus-wide steps downstream of extraction read — needs get embedded and clustered
    ACROSS pages, so they are loaded whole rather than page by page.

    Trashed and deleted pages are excluded here rather than only by :func:`prune`, because the
    window between a delete and the next extraction is exactly when their needs would do damage:
    they would cluster, be reconciled against, and route a fact to a page nobody can see. The
    rows survive that window, so a restore in it keeps its needs instead of buying them again.
    """
    with session() as s:
        rows = s.execute(
            _select_stored().where(WikiDocId.deleted_at.is_(None)).order_by(WikiDocId.path)
        ).all()
    return [StoredNeeds(*row) for row in rows]


def delete(path: str) -> None:
    doc_id = doc_ids.id_for_path(path)
    if doc_id is None:
        return
    with session() as s:
        s.execute(sa_delete(PageNeeds).where(PageNeeds.doc_id == doc_id))


def prune(live_paths: set[str], *, prefix: str = "") -> int:
    """Drop rows whose page is no longer live. Returns how many went.

    Needs of a deleted page are worse than useless downstream: they would cluster and be
    reconciled against, so a fact could be routed to a page that is gone.

    ``prefix`` bounds what may be dropped, and a scoped caller MUST pass it. ``live_paths``
    only ever describes the scope the caller walked, so an unscoped prune after a prefixed run
    would read every page outside that prefix as deleted and discard needs that cost an LLM
    call each — silently, since nothing downstream distinguishes "never extracted" from
    "wrongly pruned".

    Driven by the live corpus rather than by tombstones, which also self-heals the case where a
    page's id was re-minted: the row under the abandoned id matches no live page and goes.
    """
    live_ids = set(doc_ids.ids_for_paths(sorted(live_paths)).values())
    with session() as s:
        # Candidates are joined to their current path so scoping follows moves, like load_all.
        candidates = s.execute(
            select(PageNeeds.doc_id, WikiDocId.path).join(
                WikiDocId, WikiDocId.id == PageNeeds.doc_id
            )
        ).all()
        gone = {
            doc_id
            for doc_id, path in candidates
            if doc_id not in live_ids and _in_scope(path, prefix)
        }
        if not gone:
            return 0
        s.execute(sa_delete(PageNeeds).where(PageNeeds.doc_id.in_(gone)))
        return len(gone)


def _in_scope(path: str, prefix: str) -> bool:
    """Whether ``path`` is inside ``prefix``, matching how git resolves a pathspec.

    A path boundary, not a string prefix: scoping to "team" must not sweep "teamwork.md".
    """
    if not prefix:
        return True
    prefix = prefix.rstrip("/")
    return path == prefix or path.startswith(f"{prefix}/")
