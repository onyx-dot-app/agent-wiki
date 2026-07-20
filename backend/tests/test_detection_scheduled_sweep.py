"""Scheduled Auto Organize sweeps — the two cadence crons gate on the settings
row, not on the cron cadence itself.

Both crons fire on their fixed schedule; whether a sweep actually runs is
decided at fire time by ``ai_management_settings`` (kill switch + schedule), so
an admin flipping the schedule takes effect on the next fire.
"""
from __future__ import annotations

import pytest

from app.tasks import detection
from app.tasks.queues import detection_queue
from app.wiki.automanage import settings


@pytest.fixture
def swept(monkeypatch):
    """Run the cadence crons synchronously and capture ``runner.run_sweep``
    calls instead of running a real sweep. The task decorators enqueue by
    default, so ``immediate_mode`` is what runs the handler body inline."""
    calls: list[str | None] = []
    monkeypatch.setattr(
        detection.runner,
        "run_sweep",
        lambda *, triggered_by_user_id: calls.append(triggered_by_user_id),
    )
    with detection_queue.immediate_mode():
        yield calls


def test_daily_runs_only_when_schedule_daily(tmp_db, swept):
    settings.update(schedule="daily")
    detection.scheduled_daily_sweep()
    detection.scheduled_weekly_sweep()
    assert swept == [None]  # daily fired (system run), weekly skipped


def test_weekly_runs_only_when_schedule_weekly(tmp_db, swept):
    settings.update(schedule="weekly")
    detection.scheduled_weekly_sweep()
    detection.scheduled_daily_sweep()
    assert swept == [None]  # weekly fired, daily skipped


def test_off_schedule_never_sweeps(tmp_db, swept):
    # default schedule is "off"
    detection.scheduled_daily_sweep()
    detection.scheduled_weekly_sweep()
    assert swept == []


def test_kill_switch_overrides_schedule(tmp_db, swept):
    settings.update(enabled=False, schedule="daily")
    detection.scheduled_daily_sweep()
    assert swept == []  # frozen despite a daily schedule
