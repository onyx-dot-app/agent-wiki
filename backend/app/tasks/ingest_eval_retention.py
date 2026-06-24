"""Daily retention sweep for the ``ingest_eval_samples`` table.

Opt-in eval logging (``INGEST_EVAL_LOGGING``) is the dominant writer to the
database — the table is the vast majority of it and only grows. This periodic
task prunes rows older than ``INGEST_EVAL_RETENTION_DAYS`` (default 90) so the
table stays bounded.

Runs on ``lightweight_maintenance_queue``: each sweep is a single bounded,
indexed ``DELETE`` (no LLM, no external HTTP, no wiki commits), and the daily
cadence keeps steady-state deletes small. A retention of 0 disables the sweep.

See ``app/tasks/queues.py`` for the queue rationale.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from app.config import CONFIG
from app.ingest import eval_sample
from app.tasks.queue import crontab
from app.tasks.queues import lightweight_maintenance_queue

log = logging.getLogger(__name__)


# 11:00 UTC == 03:00 PST (UTC-8). The scheduler evaluates cron in UTC and does
# not follow DST, so this lands at 03:00 Pacific in winter and 04:00 in summer.
@lightweight_maintenance_queue.periodic_task(crontab(hour="11", minute="0"))
def prune_ingest_eval_samples() -> None:
    days = CONFIG.ingest_eval_retention_days
    if days <= 0:
        return
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    deleted = eval_sample.delete_older_than(cutoff)
    if deleted:
        log.info(
            "ingest_eval retention: deleted %d rows older than %s (%dd)",
            deleted, cutoff, days,
        )
    if deleted >= eval_sample.RETENTION_BATCH:
        # Hit the per-sweep cap — a backlog remains; the next daily run continues.
        log.warning(
            "ingest_eval retention: hit per-sweep cap (%d), backlog remains",
            eval_sample.RETENTION_BATCH,
        )
