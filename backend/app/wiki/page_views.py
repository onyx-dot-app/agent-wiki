"""Last-viewed tracking for wiki pages — the read-side half of staleness.

One row per live page: when anyone last *read* it — a human opening the page
or an agent (chat/MCP) reading it. **Keyed by stable doc id**, not path:
renames need no re-keying, restore-from-Trash keeps history, and a page
recreated at an old path is a new id that inherits nothing. Postgres-only,
like the other governance metadata.

Consumers think in months (the stale-page detector's floor is ~30 days), so
freshness is deliberately coarse: ``note_view`` throttles both in-process
(hot pages don't churn the queue) and in SQL (a row younger than the window
isn't rewritten). A page with no row has no *recorded* view — callers treat
the tracking-enable date as the floor, never "never viewed".
"""
from __future__ import annotations

import threading
import time
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.db.models import PageView
from app.db.session import session
from app.wiki import doc_ids

# A view refreshes the stored timestamp only when it's older than this.
THROTTLE = timedelta(hours=1)

# Process-local pre-check so a hot page enqueues at most one touch per
# window per process (approximate on purpose — extra writes are harmless).
_last_enqueued: dict[str, float] = {}
_lock = threading.Lock()
_MAX_LOCAL = 4096


def _now_text() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")


def should_enqueue(path: str) -> bool:
    """Cheap in-process gate for read paths: at most one queued touch per
    page per throttle window per process. Path-keyed — it runs before the
    id is resolved, and approximate is fine here."""
    now = time.monotonic()
    window = THROTTLE.total_seconds()
    with _lock:
        last = _last_enqueued.get(path)
        if last is not None and now - last < window:
            return False
        _last_enqueued[path] = now
        if len(_last_enqueued) > _MAX_LOCAL:
            cutoff = now - window
            for p, t in list(_last_enqueued.items()):
                if t < cutoff:
                    del _last_enqueued[p]
    return True


def touch(path: str) -> None:
    """Record a view of the page at ``path`` now — resolves the stable id
    (minting for pre-id pages), then upserts; a row younger than the throttle
    window is left alone (months-scale consumers; no hot-row churn)."""
    doc_id = doc_ids.get_or_mint(path)
    now = _now_text()
    floor = (datetime.now(UTC) - THROTTLE).strftime("%Y-%m-%d %H:%M:%S")
    with session() as s:
        stmt = pg_insert(PageView).values(doc_id=doc_id, last_viewed_at=now)
        stmt = stmt.on_conflict_do_update(
            index_elements=[PageView.doc_id],
            set_={"last_viewed_at": now},
            where=(PageView.last_viewed_at < floor),
        )
        s.execute(stmt)


def last_viewed(paths: Iterable[str]) -> dict[str, str]:
    """``{path: last_viewed_at}`` for the given live paths — resolved through
    their stable ids. Paths with no id or no recorded view are absent (treat
    the tracking-enable date as their floor)."""
    wanted = [p for p in paths]
    if not wanted:
        return {}
    by_path = doc_ids.ids_for_paths(wanted)
    if not by_path:
        return {}
    id_to_path = {v: k for k, v in by_path.items()}
    with session() as s:
        rows = s.execute(
            select(PageView.doc_id, PageView.last_viewed_at).where(
                PageView.doc_id.in_(list(id_to_path))
            )
        ).all()
    return {id_to_path[row[0]]: row[1] for row in rows}


def reset_for_tests() -> None:
    with _lock:
        _last_enqueued.clear()
