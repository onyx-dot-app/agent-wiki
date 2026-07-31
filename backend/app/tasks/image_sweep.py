"""Daily retention sweep for unreferenced wiki images.

Images live in Postgres, so dereferenced blobs need a periodic sweep to keep
storage bounded without touching the wiki commit path.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from app.metrics import (
    wiki_image_sweep_deleted_total,
    wiki_images_bytes_total,
    wiki_images_total,
)
from app.tasks.queue import crontab
from app.tasks.queues import lightweight_maintenance_queue
from app.tasks.trash_purge import TRASH_RETENTION_DAYS
from app.wiki import coedit, doc_ids, git as wiki_git, image_store

log = logging.getLogger(__name__)

_CREATION_GRACE = timedelta(hours=24)
# Image dereference retention stays aligned with trash retention.
_DEREFERENCE_RETENTION_DAYS = TRASH_RETENTION_DAYS
_TEXT_TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S"


def _now_text() -> str:
    return datetime.now(timezone.utc).strftime(_TEXT_TIMESTAMP_FORMAT)


def _parse(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.strptime(ts, _TEXT_TIMESTAMP_FORMAT).replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _editing_doc_ids() -> set[str]:
    """Doc ids of pages with a live co-edit session."""
    paths = coedit.active_session_paths()
    return set(doc_ids.ids_for_paths(list(paths)).values()) if paths else set()


def _referenced(rows: list[image_store.ImageSweepRow]) -> set[str]:
    """Ids whose serving URL is in the working tree, or whose anchor page is
    being edited. A live Yjs buffer has no server-readable text, so the open
    session stands in for its draft's references.
    """
    urls = {row.id: image_store.serving_url(row.id) for row in rows}
    matched = wiki_git.grep_working_tree_url_bounded(urls.values())
    editing = _editing_doc_ids()
    return {
        row.id
        for row in rows
        if urls[row.id] in matched or row.anchor_doc_id in editing
    }


def _refresh_gauges() -> None:
    count, total_bytes = image_store.totals()
    wiki_images_total.set(count)
    wiki_images_bytes_total.set(total_bytes)


# 12:00 UTC == 04:00 PST (UTC-8). The scheduler evaluates cron in UTC and does
# not follow DST, so this lands at 04:00 Pacific in winter and 05:00 in summer.
@lightweight_maintenance_queue.periodic_task(crontab(hour="12", minute="0"))
def sweep_wiki_images() -> None:
    deleted = 0
    try:
        candidates = image_store.list_for_sweep()
        if not candidates:
            log.info("image sweep: scanned=0 referenced=0 flagged=0 cleared=0 deleted=0")
            return

        referenced_ids = _referenced(candidates)

        now = datetime.now(timezone.utc)
        # 0 disables deletion, matching the trash-purge retention contract.
        deletion_enabled = _DEREFERENCE_RETENTION_DAYS > 0
        now_text = _now_text()
        flagged = 0
        cleared = 0
        for candidate in candidates:
            if candidate.id in referenced_ids:
                if candidate.unreferenced_since is not None:
                    image_store.set_unreferenced_since(candidate.id, None)
                    cleared += 1
                continue

            if candidate.unreferenced_since is None:
                created_at = _parse(candidate.created_at)
                if created_at is not None and now - created_at > _CREATION_GRACE:
                    image_store.set_unreferenced_since(candidate.id, now_text)
                    flagged += 1
                continue

            unreferenced_since = _parse(candidate.unreferenced_since)
            if unreferenced_since is None or not deletion_enabled:
                continue
            if now - unreferenced_since > timedelta(days=_DEREFERENCE_RETENTION_DAYS):
                # The commit lock serializes this re-check + delete against
                # every page writer, so no commit can slip a reference in
                # between.
                try:
                    with wiki_git.commit_lock():
                        if candidate.id in _referenced([candidate]):
                            image_store.set_unreferenced_since(candidate.id, None)
                            cleared += 1
                            continue
                        if image_store.delete_if_anchor_idle(candidate.id):
                            deleted += 1
                except Exception:
                    log.exception("image sweep: delete failed for %s", candidate.id)

        if deleted:
            wiki_image_sweep_deleted_total.inc(deleted)
        log.info(
            "image sweep: scanned=%d referenced=%d flagged=%d cleared=%d deleted=%d",
            len(candidates),
            len(referenced_ids),
            flagged,
            cleared,
            deleted,
        )
    finally:
        _refresh_gauges()
