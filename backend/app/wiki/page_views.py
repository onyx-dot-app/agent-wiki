"""Last-viewed tracking for wiki pages — the read-side half of staleness.

A single ``last_viewed_at`` attribute on the page's stable-id row
(``wiki_doc_ids``): when anyone last *read* the page — a human opening it or
an agent (chat/MCP) reading it at HEAD. Not an event log (that would earn
its own table); renames need no re-keying, restore-from-Trash keeps the
value, and a page recreated at an old path is a new id row starting at NULL.

Consumers think in months (the stale-page detector's floor), so freshness is
deliberately coarse: reads pre-gate in-process (hot pages don't churn the
queue) and the UPDATE itself skips rows younger than the window. A NULL has
no *recorded* view — callers treat the tracking-enable date as the floor
(``tracking_floor``), never "never viewed".
"""
from __future__ import annotations

import logging
import threading
import time
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, or_, select, update as sa_update

from app.db.models import WikiDocId
from app.db.session import session
from app.wiki import doc_ids

# A view refreshes the stored timestamp only when it's older than this.
THROTTLE = timedelta(hours=1)

# Process-local pre-check so a hot page enqueues at most one touch per
# window per process (approximate on purpose — extra writes are harmless).
_last_enqueued: dict[str, float] = {}
_lock = threading.Lock()
_MAX_LOCAL = 4096

log = logging.getLogger(__name__)


def _now_text() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")


def _should_record(path: str) -> bool:
    """Cheap in-process gate: at most one write per page per throttle window
    per process."""
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


def note_view(path: str) -> None:
    """Record a view from a read path: throttled, and failure-proof — view
    bookkeeping must never surface near a page read. Inline on purpose (no
    queue): the write is one guarded UPDATE at most once per page per hour,
    and the read path already writes to this table (id minting)."""
    if not _should_record(path):
        return
    try:
        touch(path)
    except Exception:
        log.warning("page view stamp failed for %s", path, exc_info=True)


def touch(path: str) -> None:
    """Record a view of the page at ``path`` now — resolves the stable id
    (minting for pre-id pages), then updates; a value younger than the
    throttle window is left alone (months-scale consumers, no hot-row
    churn)."""
    doc_id = doc_ids.get_or_mint(path)
    floor = (datetime.now(UTC) - THROTTLE).strftime("%Y-%m-%d %H:%M:%S")
    with session() as s:
        s.execute(
            sa_update(WikiDocId)
            .where(
                WikiDocId.id == doc_id,
                or_(
                    WikiDocId.last_viewed_at.is_(None),
                    WikiDocId.last_viewed_at < floor,
                ),
            )
            .values(last_viewed_at=_now_text())
        )


def last_viewed(paths: Iterable[str]) -> dict[str, str]:
    """``{path: last_viewed_at}`` for the given live paths. Paths with no
    recorded view are absent (treat ``tracking_floor`` as their floor)."""
    wanted = list(paths)
    if not wanted:
        return {}
    with session() as s:
        rows = s.execute(
            select(WikiDocId.path, WikiDocId.last_viewed_at).where(
                WikiDocId.path.in_(wanted),
                WikiDocId.deleted_at.is_(None),
                WikiDocId.last_viewed_at.is_not(None),
            )
        ).all()
    return {row[0]: row[1] for row in rows}


def tracking_floor() -> str | None:
    """The oldest recorded view — a proxy for when view tracking went live.
    ``None`` when nothing has been recorded yet. Consumers that treat "no
    recorded view" as "not viewed in a long time" must first check this
    floor is itself old enough; before that, absence only means tracking
    is young."""
    with session() as s:
        return s.execute(select(func.min(WikiDocId.last_viewed_at))).scalar()


def reset_for_tests() -> None:
    with _lock:
        _last_enqueued.clear()
