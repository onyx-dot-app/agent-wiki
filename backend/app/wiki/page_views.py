"""Last-viewed tracking for wiki pages — the read-side half of staleness.

One row per live page path: when anyone last *read* it — a human opening the
page or an agent (chat/MCP) reading it. Postgres-only metadata like
``update_policies``: never in the wiki repo, re-keyed on moves, dropped on
deletes.

Consumers think in months (the stale-page detector's floor is ~180 days), so
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

from sqlalchemy import delete as sa_delete
from sqlalchemy import select, update as sa_update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.db.models import PageView
from app.db.session import session
from app.models.wiki import PathMove

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
    page per throttle window per process."""
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
    """Record a view of ``path`` now — upsert, but leave a row younger than
    the throttle window alone (months-scale consumers; no hot-row churn)."""
    now = _now_text()
    floor = (datetime.now(UTC) - THROTTLE).strftime("%Y-%m-%d %H:%M:%S")
    with session() as s:
        stmt = pg_insert(PageView).values(path=path, last_viewed_at=now)
        stmt = stmt.on_conflict_do_update(
            index_elements=[PageView.path],
            set_={"last_viewed_at": now},
            where=(PageView.last_viewed_at < floor),
        )
        s.execute(stmt)


def last_viewed(paths: Iterable[str]) -> dict[str, str]:
    """``{path: last_viewed_at}`` for the given paths — missing paths have no
    recorded view (treat the tracking-enable date as their floor)."""
    wanted = list(paths)
    if not wanted:
        return {}
    with session() as s:
        rows = s.execute(
            select(PageView.path, PageView.last_viewed_at).where(
                PageView.path.in_(wanted)
            )
        ).all()
    return {row[0]: row[1] for row in rows}


def on_path_moved(moves: list[PathMove]) -> None:
    """Re-key rows for moved pages so view history follows the page."""
    with session() as s:
        for mv in moves:
            # A stale row at the destination (deleted page's residue) loses
            # to the moving page's history.
            s.execute(sa_delete(PageView).where(PageView.path == mv.new))
            s.execute(
                sa_update(PageView)
                .where(PageView.path == mv.old)
                .values(path=mv.new)
            )


def on_page_deleted(path: str) -> None:
    """Drop the row — a recreated page at this path is a new document and
    must not inherit old view history."""
    with session() as s:
        s.execute(sa_delete(PageView).where(PageView.path == path))
