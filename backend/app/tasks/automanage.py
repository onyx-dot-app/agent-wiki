"""Wiki Auto Management tasks — split across the two automanage queues by
latency tier (see ``app/tasks/queues.py`` and the "Queues and Workers" design
doc).

Sweeps are emit-only (they write ``change_proposals``, never git). Execution —
human or AI-managed — is the only thing that commits, and goes through
``executor.execute``; the two thin task bindings differ only in queue:
human-approved on the nearline queue (a human is waiting), AI auto-approved on
the offline queue (batch, with the sweeps that produced them).
"""
from __future__ import annotations

import logging

from app.tasks.queue import crontab
from app.tasks.queues import automanage_nearline_queue, automanage_offline_queue
from app.wiki.automanage import executor, runner, settings

log = logging.getLogger(__name__)


@automanage_offline_queue.task()
def run_detection_sweep(triggered_by_user_id: str | None) -> None:
    """Whole-space detection sweep. ``triggered_by_user_id`` is the admin who
    kicked it off (NULL for a system/scheduled run)."""
    runner.run_sweep(triggered_by_user_id=triggered_by_user_id)


@automanage_offline_queue.task()
def run_detection_on_create(path: str, creator_user_id: str | None) -> None:
    """Focused detection for a just-created page (see ``runner.run_on_create``).
    Offline: the creator isn't blocked on it, and it rides the same queue as
    the sweeps whose detectors it reuses. ``creator_user_id`` is recorded as
    the run's trigger attribution.

    State-based on purpose: the run checks ``path`` against wiki state at
    execution time, not a snapshot from the creation event. A proposal is a
    claim about *current* state — if the page was edited into uniqueness,
    deleted, or moved before a delayed task runs, there is respectively
    nothing true to propose, nothing to scan (singleton neighborhood
    no-ops), or a skipped check the next sweep covers. Anchoring to
    creation-time content would emit exactly the stale-premise proposals
    the validate()/base-sha machinery exists to retire."""
    runner.run_on_create(path, triggered_by_user_id=creator_user_id)


@automanage_nearline_queue.task()
def execute_approved_proposal(proposal_id: int) -> None:
    """Apply a **human-approved** proposal (commits to git). Nearline — a human
    approved it and is waiting, so it applies promptly, not behind a sweep."""
    executor.execute(proposal_id)


@automanage_offline_queue.task()
def execute_auto_approved_proposal(proposal_id: int) -> None:
    """Apply an **AI auto-approved** proposal (commits to git). Offline — rides
    the automanage batch queue with the sweeps that produced it."""
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
@automanage_offline_queue.periodic_task(crontab(hour="8", minute="0"))
def scheduled_daily_sweep() -> None:
    """Daily recurring sweep — runs only when ``schedule`` is ``daily``."""
    _run_scheduled_sweep("daily")


@automanage_offline_queue.periodic_task(crontab(day_of_week="1", hour="8", minute="0"))
def scheduled_weekly_sweep() -> None:
    """Weekly recurring sweep (Mondays) — runs only when ``schedule`` is
    ``weekly``."""
    _run_scheduled_sweep("weekly")
