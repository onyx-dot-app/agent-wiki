"""Wiki Auto Management tasks — split across two dedicated queues (see
``app/tasks/queues.py`` for the rationale).

- ``automanage_queue`` — the autonomous pipeline: whole-space sweeps (on-demand
  + the recurring cadence crons) and the AI-managed auto-apply executes they
  fan out. All unattended/batch.
- ``automanage_execute_queue`` — human-approved executes only, on their own
  responsive worker so an approval applies promptly rather than head-of-line
  blocking behind an in-flight sweep.

Sweeps are emit-only (they write ``change_proposals``, never git); execution —
whether human or AI — is the only thing that commits, and it goes through
``executor.execute``.
"""
from __future__ import annotations

import logging

from app.tasks.queue import crontab
from app.tasks.queues import automanage_execute_queue, automanage_queue
from app.wiki.automanage import executor, runner, settings

log = logging.getLogger(__name__)


@automanage_queue.task()
def run_detection_sweep(triggered_by_user_id: str | None) -> None:
    """Whole-space detection sweep. ``triggered_by_user_id`` is the admin who
    kicked it off (NULL for a system/scheduled run)."""
    runner.run_sweep(triggered_by_user_id=triggered_by_user_id)


@automanage_execute_queue.task()
def apply_approved_proposal(proposal_id: int) -> None:
    """Apply a **human-approved** proposal (commits to git). On the responsive
    ``automanage_execute`` queue so an approval applies immediately, never
    behind an in-flight sweep or a batch of AI auto-applies."""
    executor.execute(proposal_id)


@automanage_queue.task()
def apply_auto_approved_proposal(proposal_id: int) -> None:
    """Apply an **AI-auto-approved** proposal (commits to git). Rides the
    ``automanage`` batch queue with the sweeps — background, queue-tolerant."""
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
@automanage_queue.periodic_task(crontab(hour="8", minute="0"))
def scheduled_daily_sweep() -> None:
    """Daily recurring sweep — runs only when ``schedule`` is ``daily``."""
    _run_scheduled_sweep("daily")


@automanage_queue.periodic_task(crontab(day_of_week="1", hour="8", minute="0"))
def scheduled_weekly_sweep() -> None:
    """Weekly recurring sweep (Mondays) — runs only when ``schedule`` is
    ``weekly``."""
    _run_scheduled_sweep("weekly")
