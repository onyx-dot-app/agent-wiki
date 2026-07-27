"""Record wiki page views — a single throttled upsert per task.

Rides ``lightweight_maintenance_queue`` (sub-second, no LLM, no external
HTTP, no wiki commits). Read paths pre-gate with
``page_views.should_enqueue`` so hot pages enqueue at most one touch per
throttle window per process.
"""
from __future__ import annotations

import logging

from app.tasks.queues import lightweight_maintenance_queue
from app.wiki import page_views

log = logging.getLogger(__name__)


@lightweight_maintenance_queue.task()
def record_page_view(path: str) -> None:
    """Stamp ``last_viewed_at`` for ``path`` (coarse — see the repo module)."""
    try:
        page_views.touch(path)
    except Exception:
        # Never let view bookkeeping surface anywhere near a read path.
        log.warning("page view touch failed for %s", path, exc_info=True)
