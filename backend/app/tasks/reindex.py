"""Index wiki documents in OpenSearch for BM25 full-text search.

Three entry points:

  index_path(path)        — async task on lightweight_maintenance_queue;
                            used by after_doc_write / after_path_move.
  index_path_inline(path) — synchronous version; tests call this directly
                            so they can assert on search results immediately.
  reconcile_bm25_index()    — hourly cron; re-indexes any .md files touched
                              in the last 2 hours to heal missed events.

All three swallow exceptions so a broken OpenSearch never aborts a doc
write or hourly sweep.  Errors are logged at WARNING level.

Neither function depends on the ``documents`` table — only the git
working tree is consulted for content and path existence.
"""
from __future__ import annotations

import logging
import re

from app.db import fts
from app.metrics import wiki_pages_total
from app.tasks.queue import crontab
from app.tasks.queues import lightweight_maintenance_queue
from app.wiki import git as wiki_git

log = logging.getLogger(__name__)

_H1_RE = re.compile(r"^#{1,2}\s+(.+)", re.MULTILINE)


def _extract_title(body: str) -> str:
    """Return the first # or ## heading, or empty string."""
    m = _H1_RE.search(body)
    return m.group(1).strip() if m else ""


# --------------------------------------------------------------------------- #
# Per-document reindex                                                         #
# --------------------------------------------------------------------------- #


@lightweight_maintenance_queue.task()
def index_path(path: str) -> None:
    index_path_inline(path)


def index_path_inline(path: str) -> None:
    if not path.endswith(".md"):
        return

    try:
        body = wiki_git.read_file(path)
    except Exception:
        log.warning("index_path_inline: could not read %s, removing from index", path)
        fts.delete_document(path)
        return

    fts.upsert_document(path, path, _extract_title(body), body)
    count = fts.count_documents()
    if count is not None:
        wiki_pages_total.set(count)


# --------------------------------------------------------------------------- #
# Hourly reconcile sweep                                                       #
# --------------------------------------------------------------------------- #


def reindex_all_inline() -> None:
    """Index every .md page currently in the wiki.

    Called at backend startup so freshly-seeded pages (and any pages that
    missed indexing due to a worker race at boot) are searchable immediately
    without waiting for the hourly reconcile or the worker queue.

    Upserts are idempotent — re-indexing an already-indexed page is safe.
    """
    paths = [p for p in wiki_git.list_paths() if p.endswith(".md")]
    if not paths:
        return
    log.info("reindex: indexing %d wiki page(s) at startup", len(paths))
    for path in paths:
        index_path_inline(path)


@lightweight_maintenance_queue.periodic_task(crontab(minute="0"))
def reconcile_bm25_index() -> None:
    """Re-index any .md files touched in the last 2 hours."""
    touched = {p for p in wiki_git.paths_touched_since("2 hours ago") if p.endswith(".md")}
    if not touched:
        return

    for path in touched:
        try:
            body = wiki_git.read_file(path)
            fts.upsert_document(path, path, _extract_title(body), body)
        except Exception:
            log.warning("reconcile_bm25_index: failed to reindex %s", path, exc_info=True)
