"""Too-frequent-update guardrail — surface churn in the activity feed.

Part of "Taming Bad-Behaved Wikis" (see the wiki design page). After an
ingestion auto-update commits a page, ``after_doc_write`` enqueues
``check_update_frequency`` here. It counts the page's ingestion updates in a
**sliding 24h window** (``git log --since=now-24h`` by the ``Onyx Ingest``
author) and, on the commit that crosses the page's warning threshold, records a
``wiki.frequent_updates`` event so it shows in the owner's Activities panel.

Advisory only — it does not block or disable anything. (Enforcing the admin cap
by pausing over-cap ingestion is a follow-up PR.) The event is recorded once
per crossing (when ``count == threshold``) so a hot page doesn't spam the feed.
All work here is a git read + one Postgres insert — no LLM, no wiki commit.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

from app.db.models import Event
from app.db.session import session
from app.tasks.queues import lightweight_maintenance_queue
from app.wiki import constants as wiki_constants
from app.wiki import git as wiki_git
from app.wiki import update_policy

log = logging.getLogger(__name__)

_WINDOW_HOURS = 24

EVENT_FREQUENT_UPDATES = "wiki.frequent_updates"


@lightweight_maintenance_queue.task()
def check_update_frequency(path: str) -> None:
    _check_update_frequency_inline(path)


def _check_update_frequency_inline(path: str) -> None:
    if not path.endswith(".md"):
        return
    since = (datetime.now(timezone.utc) - timedelta(hours=_WINDOW_HOURS)).isoformat()
    count = wiki_git.count_commits_since(
        path, author=wiki_constants.INGEST_AUTHOR_EMAIL, since_iso=since
    )
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
    with session() as s:
        s.add(
            Event(
                kind=EVENT_FREQUENT_UPDATES,
                actor=wiki_constants.INGEST_AUTHOR,
                target=path,
                payload_json=json.dumps(
                    {"doc_path": path, "count": count, "threshold": threshold}
                ),
            )
        )
