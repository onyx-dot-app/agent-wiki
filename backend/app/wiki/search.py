"""Wiki search — thin wrapper over the FTS index."""
from __future__ import annotations

import logging

from app.db import fts
from app.db.sqlite import cursor

log = logging.getLogger(__name__)


def search(query: str, limit: int = 20) -> list[dict]:
    return fts.search(query, limit=limit)


def bootstrap_index_if_empty() -> int:
    """Index every tracked .md file in the wiki repo if the FTS table is empty.

    Returns the number of docs indexed. Synchronous so the search tool works
    on first request even before the Huey worker has consumed its queue.
    """
    with cursor() as cur:
        count = cur.execute("SELECT COUNT(*) FROM documents_fts").fetchone()[0]
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
