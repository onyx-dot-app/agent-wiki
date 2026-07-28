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

from app.db import fts, page_embeddings
from app.db.session import advisory_xact_lock, session
from app.llm import embeddings
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
# Page embeddings (ingestion relevance filter)                                #
#                                                                             #
# Ride the reindex path: the same walk that keeps OpenSearch fresh keeps the  #
# per-page embedding store fresh. All best-effort and gated on an OpenAI key  #
# (embeddings.available()) — a failure (or no key configured) never affects   #
# BM25 indexing or a doc commit.                                              #
# --------------------------------------------------------------------------- #


def _embed_page(path: str, body: str) -> None:
    """Embed a page body into ``page_embeddings`` when an OpenAI key is configured.

    Skips re-embedding when the (capped) body hash is unchanged, so ordinary
    commits and the hourly sweep don't re-hit the embedding API for pages that
    didn't change. Swallows all errors."""
    if not embeddings.available():
        return
    try:
        capped = body[: embeddings.PAGE_CHAR_CAP]
        sha = embeddings.content_sha256(capped)
        if page_embeddings.get_sha(path) == sha:
            return  # unchanged — skip re-embed
        vec = embeddings.embed_text(capped)
        if vec is None:
            return
        page_embeddings.upsert(path, sha, embeddings.model_name(), embeddings.pack(vec))
    except Exception:
        log.warning("reindex: embedding page %s failed", path, exc_info=True)


def drop_page_embedding(path: str) -> None:
    """Remove a page's stored embedding (page delete / move-away). Best-effort;
    a no-op when no OpenAI key is configured (nothing was ever stored)."""
    if not embeddings.available():
        return
    try:
        page_embeddings.delete(path)
    except Exception:
        log.warning("reindex: dropping embedding for %s failed", path, exc_info=True)


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
        drop_page_embedding(path)
        return

    fts.upsert_document(path, path, _extract_title(body), body)
    _embed_page(path, body)


# --------------------------------------------------------------------------- #
# Hourly reconcile sweep                                                       #
# --------------------------------------------------------------------------- #


def reindex_all_inline() -> None:
    """Index every .md page currently in the wiki. Idempotent.

    Runs from the seed path (fresh wiki); a boot over pre-existing content
    is covered by ``backfill_unindexed_pages``, which skips pages already
    indexed.
    """
    paths = [p for p in wiki_git.list_paths() if p.endswith(".md")]
    if not paths:
        return
    log.info("reindex: indexing %d wiki page(s) at startup", len(paths))
    for path in paths:
        index_path_inline(path)


# ASCII "ridx" — must stay distinct from other advisory-lock keys (e.g. the
# triggers rebuild lock in app/triggers/repo.py).
_BACKFILL_ADVISORY_LOCK = 0x72696478


def backfill_unindexed_pages() -> None:
    """Startup backfill: index every tracked .md page missing from the index.

    Covers a first boot over pre-existing content (restored volume,
    migration, an already-populated ``WIKI_DIR``) — that path skips seeding,
    which is the only other boot path that indexes — and a partially
    restored index, where some pages are present and the rest would stay
    invisible to search until edited. Steady-state boots find no missing
    pages and index nothing.

    Concurrent replicas booting against the same index serialise on a
    Postgres advisory lock, and the missing set is computed under it — so
    followers observe the leader's writes and no-op instead of repeating
    the walk (and its embedding calls).

    ``count_documents()`` returns None when OpenSearch is unreachable —
    skip then, since indexing would fail just the same; the next boot
    retries. ``paths_under("")`` caps at 10k entries, so beyond that the
    missing set over-approximates and re-indexes some already-indexed
    pages — harmless (upserts are idempotent, embeddings skip on an
    unchanged hash), just slower.
    """
    if fts.count_documents() is None:
        return
    tracked = {p for p in wiki_git.list_paths() if p.endswith(".md")}
    if not tracked:
        return
    try:
        with session() as s:
            advisory_xact_lock(s, _BACKFILL_ADVISORY_LOCK)
            missing = sorted(tracked - set(fts.paths_under("")))
            if not missing:
                return
            log.info("reindex: backfilling %d unindexed wiki page(s)", len(missing))
            for path in missing:
                index_path_inline(path)
    except Exception:
        log.warning("reindex: startup backfill failed", exc_info=True)


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
            _embed_page(path, body)
        except Exception:
            log.warning("reconcile_bm25_index: failed to reindex %s", path, exc_info=True)
