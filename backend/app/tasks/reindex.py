"""Reindex a single wiki doc into the BM25 search index, plus the
hourly reconciliation sweep that catches anything missed.

Tasks in this module run on the ``lightweight_maintenance_queue`` queue
— the cheap, LLM-free queue for fast upkeep work. Online indexing is
triggered after every wiki write (human edit, agent edit, move,
doc-updater commit) and on demand via ``POST /api/documents/reindex``.
The hourly ``reconcile_bm25_index`` periodic task is a safety net: it
walks the wiki tree, compares HEAD shas to the ``indexed_sha`` column
on each FTS row, and re-enqueues anything that drifted (lost pgmq
message, worker crash mid-handler, missing post-commit hook).

See ``app/tasks/queues.py`` for the queue rationale.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.db import fts
from app.db.models import BM25ReconcileState, DocumentFts
from app.db.session import session
from app.tasks.queue import QueueFullError, crontab
from app.tasks.queues import lightweight_maintenance_queue
from app.wiki import git

log = logging.getLogger(__name__)

# Overlap the previous window slightly so a commit that landed just
# before the cursor was stamped doesn't slip between two runs.
_RECONCILE_WINDOW_OVERLAP = timedelta(minutes=5)


def _derive_title(path: str, body: str) -> str:
    for line in body.splitlines():
        s = line.strip()
        if s.startswith("# "):
            return s[2:].strip()
    return Path(path).stem


def reindex_path_inline(path: str) -> None:
    """Reindex a single wiki path into the BM25 index, synchronously.

    Use this from startup bootstrap or anywhere that can't depend on a
    worker being available. Online write paths should call the
    ``reindex_path`` task wrapper instead so they don't block the
    request.
    """
    log.debug("reindex %s", path)
    body = git.read_file(path)
    fts.upsert_document(
        doc_id=path,
        path=path,
        title=_derive_title(path, body),
        body=body,
        indexed_sha=git.head_sha_for_path(path),
    )


@lightweight_maintenance_queue.task()
def reindex_document(doc_id: str, path: str, title: str) -> None:
    log.debug("reindex_document doc_id=%s path=%s", doc_id, path)
    body = git.read_file(path)
    fts.upsert_document(
        doc_id=doc_id,
        path=path,
        title=title,
        body=body,
        indexed_sha=git.head_sha_for_path(path),
    )


@lightweight_maintenance_queue.task()
def reindex_path(path: str) -> None:
    """Reindex a wiki path into the BM25 index. Path doubles as the doc_id."""
    reindex_path_inline(path)


def _read_reconcile_cursor() -> datetime | None:
    """Last successful completion timestamp, or ``None`` if never run."""
    with session() as s:
        row = s.get(BM25ReconcileState, 1)
        if row is None or not row.last_completed_at:
            return None
        raw = row.last_completed_at
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        log.warning("reconcile: bad cursor %r — treating as bootstrap", raw)
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _write_reconcile_cursor(completed_at: datetime) -> None:
    iso = completed_at.astimezone(timezone.utc).isoformat()
    stmt = pg_insert(BM25ReconcileState).values(id=1, last_completed_at=iso)
    stmt = stmt.on_conflict_do_update(
        index_elements=[BM25ReconcileState.id],
        set_={"last_completed_at": stmt.excluded.last_completed_at},
    )
    with session() as s:
        s.execute(stmt)


@lightweight_maintenance_queue.periodic_task(crontab(minute="0"))
def reconcile_bm25_index() -> None:
    """Hourly drift sweep against the wiki repo, scoped by cursor.

    Bootstrap (cursor NULL, first ever run after deploy): batched
    ``git log`` over the whole tree; also sweeps FTS for paths that no
    longer exist in the repo at all. Catches pre-migration NULL
    ``indexed_sha`` rows and any pre-existing drift.

    Windowed (cursor set): looks only at paths git touched since the
    previous successful run (with a small overlap to avoid edge
    races). Compares each candidate's HEAD sha to
    ``DocumentFts.indexed_sha``. Mismatches re-enqueue
    ``reindex_path``; window-local deletions get their FTS row removed.

    The cursor advances only on completion. ``QueueFullError`` aborts
    the run without advancing — next cycle retries the same window.
    """
    started_at = datetime.now(timezone.utc)
    cursor = _read_reconcile_cursor()

    head_shas: dict[str, str | None]
    if cursor is None:
        head_shas = {
            p: sha
            for p, sha in git.list_paths_with_head_sha(".")
            if p.endswith(".md") and sha
        }
        scope_wide = True
    else:
        since = (cursor - _RECONCILE_WINDOW_OVERLAP).astimezone(timezone.utc).isoformat()
        touched = {p for p in git.paths_touched_since(since) if p.endswith(".md")}
        head_shas = {p: git.head_sha_for_path(p) for p in touched}
        scope_wide = False

    indexed: dict[str, str | None] = {}
    with session() as s:
        if scope_wide:
            for path, indexed_sha in s.execute(
                select(DocumentFts.path, DocumentFts.indexed_sha)
            ).all():
                indexed[path] = indexed_sha
        elif head_shas:
            for path, indexed_sha in s.execute(
                select(DocumentFts.path, DocumentFts.indexed_sha).where(
                    DocumentFts.path.in_(list(head_shas.keys()))
                )
            ).all():
                indexed[path] = indexed_sha

    stale = [p for p, sha in head_shas.items() if sha and indexed.get(p) != sha]
    orphans = [p for p, sha in head_shas.items() if sha is None and p in indexed]
    if scope_wide:
        orphans.extend(
            p for p in indexed.keys() - head_shas.keys() if p.endswith(".md")
        )

    enqueued = 0
    for path in stale:
        try:
            reindex_path(path)
            enqueued += 1
        except QueueFullError:
            log.warning(
                "reconcile: queue full after %d/%d reindexes; cursor unchanged, retrying next cycle",
                enqueued,
                len(stale),
            )
            return

    for path in orphans:
        fts.delete_document(path)

    _write_reconcile_cursor(started_at)

    if stale or orphans:
        log.info(
            "reconcile: scope=%s candidates=%d enqueued=%d orphans=%d",
            "bootstrap" if scope_wide else "window",
            len(head_shas),
            enqueued,
            len(orphans),
        )
