"""Wiki search — delegates to fts module (currently stubbed pending OpenSearch)."""
from __future__ import annotations

import re

from pydantic import BaseModel

from app.db import fts
from app.db.fts import SearchHit


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
    return _NORMALIZE_RE.sub("", s).lower()


def search_folders(query: str, limit: int = 10) -> list[FolderHit]:
    """Return folder paths whose normalized name contains the normalized query.

    Derived from git-tracked ``.md`` paths. Sorted so prefix matches and
    shorter paths come first.
    """
    from app.wiki import git

    norm_q = _normalize(query)
    if not norm_q:
        return []

    rows = [p for p in git.list_paths() if p.endswith(".md")]

    folders: set[str] = set()
    for path in rows:
        parts = path.split("/")
        for i in range(1, len(parts)):
            folders.add("/".join(parts[:i]))

    matches: list[tuple[int, int, str]] = []
    for folder in folders:
        leaf = folder.rsplit("/", 1)[-1]
        norm_leaf = _normalize(leaf)
        norm_full = _normalize(folder)
        if norm_q in norm_leaf:
            rank = 0 if norm_leaf.startswith(norm_q) else 1
            matches.append((rank, len(folder), folder))
        elif norm_q in norm_full:
            matches.append((2, len(folder), folder))

    matches.sort()
    return [FolderHit(path=p) for _, _, p in matches[:limit]]
