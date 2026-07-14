"""Daily auto-purge for the Trash.

Deleting a page/folder soft-deletes it into ``.trash/`` (see ``app/wiki/trash.py``);
nothing removes it, so ``.trash/`` — and the Trash view's per-entry git walk —
would grow without bound. This sweep permanently removes entries past
``TRASH_RETENTION_DAYS``: ``git rm`` the ``.trash/<id>/`` subtree + commit (a
**soft purge** — content stays in git history, never a history rewrite) and drop
the ACL/owner/policy rows parked at the trash location.

Runs on ``documents_queue``, **not** ``lightweight_maintenance_queue``: the purge
makes a wiki commit (and takes the commit lock), which that queue's contract
forbids. A daily purge isn't latency-sensitive, so co-tenanting with the (slow,
commit-producing) doc-reconciliation queue is fine. See ``app/tasks/queues.py``.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from app.tasks.queue import crontab
from app.tasks.queues import documents_queue
from app.wiki import trash

log = logging.getLogger(__name__)

# How long a trashed item is kept before auto-purge. A constant for now
# (per-instance / admin config can follow); 0 disables the sweep.
TRASH_RETENTION_DAYS = 30

# Git identity for the purge commits.
_PURGE_AUTHOR = "Agent Wiki <system@agent-wiki>"


def _trashed_before(trashed_at: str, cutoff: datetime) -> bool:
    """True if the ISO-8601 ``trashed_at`` is strictly before ``cutoff``.
    Unparseable/empty timestamps are treated as not-expired (kept)."""
    if not trashed_at:
        return False
    try:
        dt = datetime.fromisoformat(trashed_at)
    except ValueError:
        return False
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt < cutoff


# 10:00 UTC daily — off-peak. The scheduler evaluates cron in UTC (no DST).
@documents_queue.periodic_task(crontab(hour="10", minute="0"))
def purge_expired_trash() -> None:
    if TRASH_RETENTION_DAYS <= 0:
        return
    cutoff = datetime.now(timezone.utc) - timedelta(days=TRASH_RETENTION_DAYS)
    purged = 0
    # Snapshot the list first; each purge mutates `.trash/`.
    for entry in trash.list_entries():
        if not _trashed_before(entry.trashed_at, cutoff):
            continue
        try:
            if trash.purge(entry.trash_id, actor=_PURGE_AUTHOR):
                purged += 1
        except Exception:
            log.exception("trash purge failed for %s", entry.trash_id)
    if purged:
        log.info(
            "trash purge: removed %d item(s) trashed before %s (%dd retention)",
            purged,
            cutoff.isoformat(timespec="seconds"),
            TRASH_RETENTION_DAYS,
        )
