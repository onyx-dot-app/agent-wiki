"""Postgres store for per-page information needs.

Current-valued and path-keyed: a page's needs describe that page as it is now, so a
re-extraction replaces them. That is the opposite of ``app.db.entity_taxonomy``, which is
append-only — and the difference is that nothing keys facts by a need, so there is nothing a
replacement can orphan.

The reason this is stored at all rather than recomputed is cost. Extraction is one LLM call
per page, so a corpus-wide re-run over an unchanged wiki is pure waste. :func:`stale_paths`
is what makes a re-run cost one call per edited page instead.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import delete as sa_delete, select

from app.db.models import PageNeeds
from app.db.session import session


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
    different model or taxonomy.

    All three inputs are compared, not just the body, because each changes the output. A page
    skipped after a taxonomy re-derivation would keep entity types the current taxonomy no
    longer defines — stale in a way no later step could detect.

    One query for the whole corpus; the comparison is in Python because the caller already
    holds the bodies.
    """
    if not pages:
        return []
    want_model = model or ""
    with session() as s:
        stored = {
            path: (sha, row_model, row_taxonomy)
            for path, sha, row_model, row_taxonomy in s.execute(
                select(
                    PageNeeds.path,
                    PageNeeds.content_sha256,
                    PageNeeds.model,
                    PageNeeds.taxonomy_id,
                )
            )
        }
    return [
        path
        for path, body in pages
        if stored.get(path) != (content_sha256(body), want_model, taxonomy_id)
    ]


def store(
    path: str,
    *,
    body: str,
    needs: list[dict[str, Any]],
    model: str | None = None,
    taxonomy_id: int | None = None,
) -> None:
    """Replace ``path``'s needs.

    An empty ``needs`` list is stored, not skipped: "this page tracks nothing durable" is a
    real answer, and recording it is what stops the page being re-extracted on every run.
    """
    with session() as s:
        row = s.get(PageNeeds, path)
        if row is None:
            row = PageNeeds(path=path)
            s.add(row)
        row.content_sha256 = content_sha256(body)
        row.model = model or ""
        row.taxonomy_id = taxonomy_id
        row.needs = needs
        row.updated_at = _now()


def get(path: str) -> PageNeeds | None:
    """One page's stored needs row, or None if it has never been extracted."""
    with session() as s:
        return s.get(PageNeeds, path)


def load_all() -> list[PageNeeds]:
    """Every stored row. What the corpus-wide steps downstream of extraction read — needs get
    embedded and clustered ACROSS pages, so they are loaded whole rather than per page."""
    with session() as s:
        return list(s.scalars(select(PageNeeds).order_by(PageNeeds.path)))


def delete(path: str) -> None:
    with session() as s:
        s.execute(sa_delete(PageNeeds).where(PageNeeds.path == path))


def prune(live_paths: set[str]) -> int:
    """Drop rows for pages that no longer exist. Returns how many went.

    Needs of a deleted page are worse than useless downstream: they would cluster and be
    reconciled against, so a fact could be routed to a page that is gone.
    """
    with session() as s:
        stored = set(s.scalars(select(PageNeeds.path)))
        gone = stored - live_paths
        if not gone:
            return 0
        s.execute(sa_delete(PageNeeds).where(PageNeeds.path.in_(gone)))
        return len(gone)
