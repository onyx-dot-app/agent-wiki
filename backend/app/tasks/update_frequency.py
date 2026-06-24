"""Too-frequent-update guardrails — warn owners (and cap) on ingestion churn.

Part of "Taming Bad-Behaved Wikis" (see the wiki design page). After an
ingestion auto-update commits a page, ``after_doc_write`` enqueues
``check_update_frequency`` here. It counts the page's ingestion updates in the
last 24h and, comparing against the page's warning threshold and the admin
global cap:

  - **>= cap (>0)** → turn off the page's ingestion auto-update (persisted in
    ``update_policies``) and notify the owner it was auto-disabled.
  - **>= threshold (>0)** → notify the owner the page is updating frequently.

Owner-facing notifications dedup per page (one row until dismissed). Runs on
``lightweight_maintenance_queue``: a git read + Postgres writes, no LLM and no
wiki commit.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from app.db import notifications as notifications_repo
from app.ingest import settings as ingest_settings
from app.tasks.queues import lightweight_maintenance_queue
from app.wiki import acl
from app.wiki import git as wiki_git
from app.wiki import update_policy

log = logging.getLogger(__name__)

_WINDOW_HOURS = 24

NOTIF_FREQUENT_UPDATES = "wiki.frequent_updates"
NOTIF_AUTO_UPDATE_DISABLED = "wiki.auto_update_disabled"


@lightweight_maintenance_queue.task()
def check_update_frequency(path: str) -> None:
    _check_update_frequency_inline(path)


def _page_name(path: str) -> str:
    base = path.rsplit("/", 1)[-1]
    return base[:-3] if base.endswith(".md") else base


def _check_update_frequency_inline(path: str) -> None:
    if not path.endswith(".md"):
        return

    since = (datetime.now(timezone.utc) - timedelta(hours=_WINDOW_HOURS)).isoformat()
    count = wiki_git.count_commits_since(
        path, author=wiki_git.INGEST_AUTHOR_EMAIL, since_iso=since
    )
    if count == 0:
        return

    cap = ingest_settings.get().auto_update_cap
    owner_id = acl.get_owner(path)
    link = f"/app/wiki/{path}"
    name = _page_name(path)

    # Admin hard cap applies regardless of ownership — disable, then notify the
    # owner if there is one.
    if cap > 0 and count >= cap:
        update_policy.set_policy(
            path, ingestion_auto_update_disabled=True, actor_user_id=None
        )
        log.info("auto-update cap hit for %s (count=%d cap=%d) — disabled", path, count, cap)
        if owner_id is not None:
            notifications_repo.create(
                user_id=owner_id,
                notif_type=NOTIF_AUTO_UPDATE_DISABLED,
                title="Auto-update turned off",
                description=(
                    f"“{name}” exceeded the org limit of {cap} "
                    "auto-updates per day, so auto-update was turned off. "
                    "You can turn it back on in the page's update policy."
                ),
                data={"path": path, "link": link},
            )
        return

    threshold = update_policy.resolve_warn_threshold(path)
    if owner_id is not None and threshold > 0 and count >= threshold:
        notifications_repo.create(
            user_id=owner_id,
            notif_type=NOTIF_FREQUENT_UPDATES,
            title="Page updating frequently",
            description=(
                f"“{name}” was auto-updated {count} times in the past "
                "24 hours. You can adjust the threshold or turn off auto-update "
                "in the page's update policy."
            ),
            data={"path": path, "link": link},
        )
