"""Wiki Auto Management detection tasks — bound to the dedicated ``detection``
queue (its own worker; see ``app/tasks/queues.py`` for why it isn't a
co-tenant of any other queue).

Emit-only: a sweep detects and writes ``change_proposals``; it never mutates
the wiki. Execution happens later, on approval.
"""
from __future__ import annotations

import logging

from app.tasks.queue import crontab
from app.tasks.queues import detection_queue
from app.wiki.automanage import executor, runner, settings

log = logging.getLogger(__name__)


@detection_queue.task()
def run_detection_sweep(triggered_by_user_id: str | None) -> None:
    """Whole-space detection sweep. ``triggered_by_user_id`` is the admin who
    kicked it off (NULL for a system/scheduled run)."""
    runner.run_sweep(triggered_by_user_id=triggered_by_user_id)


@detection_queue.task()
def execute_proposal(proposal_id: int) -> None:
    """Apply an approved proposal (commits to git — hence the detection queue,
    not a co-tenant)."""
    executor.execute(proposal_id)


def _run_scheduled_sweep(cadence: str) -> None:
    """Fire a system sweep only when the configured schedule matches ``cadence``
    and the kill switch is on. Both cadence crons fire on their fixed cron
    cadence regardless; the settings row is the single source of truth for
    whether a sweep actually runs, so an admin flipping the schedule takes
    effect on the next fire with no scheduler restart."""
    s = settings.get()
    if not s.enabled:
        log.info("scheduled sweep (%s): Auto Organize disabled — skip", cadence)
        return
    if s.schedule != cadence:
        return
    log.info("scheduled sweep (%s): starting", cadence)
    runner.run_sweep(triggered_by_user_id=None)


# 08:00 UTC — off-peak, ahead of the daily trash purge (10:00). Cron is
# evaluated in UTC (no DST). Weekly fires Mondays (day_of_week=1).
@detection_queue.periodic_task(crontab(hour="8", minute="0"))
def scheduled_daily_sweep() -> None:
    """Daily recurring sweep — runs only when ``schedule`` is ``daily``."""
    _run_scheduled_sweep("daily")


@detection_queue.periodic_task(crontab(day_of_week="1", hour="8", minute="0"))
def scheduled_weekly_sweep() -> None:
    """Weekly recurring sweep (Mondays) — runs only when ``schedule`` is
    ``weekly``."""
    _run_scheduled_sweep("weekly")
