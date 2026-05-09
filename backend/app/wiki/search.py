"""Wiki search — thin wrapper over the FTS index."""
from __future__ import annotations

import logging

from sqlalchemy import func, select

from app.db import fts
from app.db.fts import SearchHit
from app.db.models import DocumentFts
from app.db.session import session

log = logging.getLogger(__name__)


def search(query: str, limit: int = 20) -> list[SearchHit]:
    return fts.search(query, limit=limit)


def bootstrap_index_if_empty() -> int:
    """Index every tracked .md file in the wiki repo if the FTS table is empty.

    Returns the number of docs indexed. Synchronous so the search tool works
    on first request even before the worker has consumed its queue.
    """
    with session() as s:
        count = s.scalar(select(func.count()).select_from(DocumentFts)) or 0
    if count > 0:
        return 0

    # Imported here to avoid a circular import at module load.
    from app.tasks.reindex import reindex_path_inline
    from app.wiki.git import list_paths

    indexed = 0
    for path in list_paths():
        if not path.endswith(".md"):
            continue
        try:
            reindex_path_inline(path)
            indexed += 1
        except Exception:
            log.exception("failed to bootstrap-index %s", path)
    if indexed:
        log.info("bootstrapped FTS index with %d wiki docs", indexed)
    return indexed
