"""Too-frequent-update guardrail — surface churn in the activity feed.

Part of "Taming Bad-Behaved Wikis" (see the wiki design page). After an
ingestion auto-update commits a page, ``after_doc_write`` enqueues
``check_update_frequency`` here. It counts the page's ingestion updates in the
**sliding 24h window** and, on the commit that crosses the page's (owner-set)
warning threshold, records a ``wiki.frequent_updates`` activity event — advisory
only; the page keeps updating.

The admin **cap** is enforced separately, in the ingest pipeline
(``tasks/wiki_update.py``): an over-cap page is excluded before any LLM call.
That exclusion point calls ``record_auto_update_capped`` here to log a
``wiki.auto_update_capped`` event — meaning "an update was actually blocked",
deduped to once per over-cap episode (24h window) so a hot page doesn't flood
the feed. Recording on exclusion (rather than the crossing commit) also covers
pages that were already over the cap when an admin set/lowered it.

All work here is a git read + one Postgres insert — no LLM, no wiki commit.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select

from app.db.models import Event
from app.db.session import session
from app.tasks.queues import lightweight_maintenance_queue
from app.wiki import constants as wiki_constants
from app.wiki import git as wiki_git
from app.wiki import update_policy

log = logging.getLogger(__name__)

EVENT_FREQUENT_UPDATES = "wiki.frequent_updates"
EVENT_AUTO_UPDATE_CAPPED = "wiki.auto_update_capped"

_CAP_EVENT_DEDUP_HOURS = 24


def _record_event(kind: str, path: str, payload: dict[str, Any]) -> None:
    with session() as s:
        s.add(
            Event(
                kind=kind,
                actor=wiki_constants.INGEST_AUTHOR,
                target=path,
                payload_json=json.dumps(payload),
            )
        )


def record_auto_update_capped(path: str, count: int, cap: int) -> None:
    """Log a ``wiki.auto_update_capped`` event for a page the ingest pipeline
    just excluded for hitting the cap.

    Deduped: skips if a capped event for this page already exists within the
    trailing ``_CAP_EVENT_DEDUP_HOURS``, so a page that stays over the cap (every
    push blocked) logs once per episode, not once per blocked push."""
    cutoff = (
        datetime.now(timezone.utc) - timedelta(hours=_CAP_EVENT_DEDUP_HOURS)
    ).strftime("%Y-%m-%d %H:%M:%S")
    with session() as s:
        recent = s.scalar(
            select(Event.id)
            .where(
                Event.kind == EVENT_AUTO_UPDATE_CAPPED,
                Event.target == path,
                Event.ts >= cutoff,
            )
            .limit(1)
        )
        if recent is not None:
            return
        s.add(
            Event(
                kind=EVENT_AUTO_UPDATE_CAPPED,
                actor=wiki_constants.INGEST_AUTHOR,
                target=path,
                payload_json=json.dumps({"doc_path": path, "count": count, "cap": cap}),
            )
        )
    log.info("auto-update capped: %s at %d updates/24h (cap %d)", path, count, cap)


@lightweight_maintenance_queue.task()
def check_update_frequency(path: str) -> None:
    _check_update_frequency_inline(path)


def _check_update_frequency_inline(path: str) -> None:
    if not path.endswith(".md"):
        return
    # Same rolling 24h window as the cap exclusion and update-health, so all
    # three agree on the count.
    count = len(wiki_git.ingest_update_times_24h(path))
    if count == 0:
        return
    # Warnings are moot when the page's auto-update is off (no ingestion to
    # warn about); ingestion shouldn't even reach here, but guard anyway.
    if update_policy.resolve_for_path(path).ingestion_auto_update_disabled:
        return
    threshold = update_policy.resolve_warn_threshold(path)
    if count < threshold:
        return
    # threshold 0 → warn on every auto-update; threshold > 0 → warn once, on the
    # crossing commit (count == threshold), so a ramp-up doesn't flood the feed.
    if threshold > 0 and count != threshold:
        return
    log.info("frequent-updates: %s at %d updates/24h (threshold %d)", path, count, threshold)
    _record_event(
        EVENT_FREQUENT_UPDATES,
        path,
        {"doc_path": path, "count": count, "threshold": threshold},
    )
