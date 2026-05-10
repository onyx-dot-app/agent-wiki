"""Wiki search — BM25 over documents + lightweight folder-name match."""
from __future__ import annotations

import re

from pydantic import BaseModel
from sqlalchemy import select

from app.db import fts
from app.db.fts import SearchHit
from app.db.models import DocumentFts
from app.db.session import session


class FolderHit(BaseModel):
    path: str


def search(
    query: str,
    limit: int = 20,
    *,
    user_id: str | None = None,
    is_admin: bool = False,
    apply_visibility: bool = True,
) -> list[SearchHit]:
    return fts.search(
        query,
        limit=limit,
        user_id=user_id,
        is_admin=is_admin,
        apply_visibility=apply_visibility,
    )


_NORMALIZE_RE = re.compile(r"[\s/_\-]+")


def _normalize(s: str) -> str:
    """Lowercase and strip whitespace/separators (`/`, `-`, `_`).

    Used so a query like ``"local testing"`` matches a folder named
    ``local-testing`` or ``local_testing`` etc.
    """
    return _NORMALIZE_RE.sub("", s).lower()


def search_folders(query: str, limit: int = 10) -> list[FolderHit]:
    """Return folder paths whose normalized name contains the normalized query.

    Folder set is derived from ``documents_fts.path`` — every ancestor of
    every indexed document is a folder. Sorted so prefix matches and
    shorter paths come first; truncated to ``limit``.

    Visibility: folders aren't ACL-gated in the explorer (only documents
    are), so we don't filter here either. Page-level ACLs still apply on
    navigation.
    """
    norm_q = _normalize(query)
    if not norm_q:
        return []

    with session() as s:
        rows = s.execute(select(DocumentFts.path)).scalars().all()

    folders: set[str] = set()
    for path in rows:
        # Walk every ancestor directory of the doc path.
        parts = path.split("/")
        for i in range(1, len(parts)):
            folders.add("/".join(parts[:i]))

    matches: list[tuple[int, int, str]] = []
    for folder in folders:
        # Match against the leaf name (most useful), with a fallback to
        # the full normalized path so users can find nested folders by
        # typing a parent fragment.
        leaf = folder.rsplit("/", 1)[-1]
        norm_leaf = _normalize(leaf)
        norm_full = _normalize(folder)
        if norm_q in norm_leaf:
            # 0 = leaf prefix, 1 = leaf substring, 2 = full-path substring.
            rank = 0 if norm_leaf.startswith(norm_q) else 1
            matches.append((rank, len(folder), folder))
        elif norm_q in norm_full:
            matches.append((2, len(folder), folder))

    matches.sort()
    return [FolderHit(path=p) for _, _, p in matches[:limit]]
