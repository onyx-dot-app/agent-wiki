"""Sweep singleton — one whole-space sweep at a time.

Two concurrent sweeps emit the same drafts into the same dedup window and
double every detector's cost; the guard skips overlap without rate-limiting
the manual trigger. A stuck ``running`` corpse row (worker died mid-run)
stops blocking after the age cutoff.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.db.models import DetectionRun
from app.db.session import session
from app.wiki import git as wiki_git
from app.wiki.automanage import runner, runs
from app.wiki.automanage.detectors.empty_folder import _EmptyFolderDetector


@pytest.fixture
def repo(tmp_repo, tmp_config):
    wiki_git.commit_file("team/plan.md", "# Plan\n", "seed", author=None)
    wiki_git.commit_file("hollow/.gitkeep", "", "empty", author=None)
    return tmp_config


@pytest.fixture(autouse=True)
def _eager_detector(monkeypatch):
    monkeypatch.setattr(runner, "DETECTORS", [_EmptyFolderDetector(min_age_days=0)])


def _plant_running_sweep(*, hours_old: float) -> None:
    started = (datetime.now(UTC) - timedelta(hours=hours_old)).strftime(
        "%Y-%m-%d %H:%M:%S"
    )
    with session() as s:
        s.add(
            DetectionRun(
                id=f"run_test_{hours_old}",
                trigger="sweep",
                status="running",
                started_at=started,
            )
        )


def test_second_sweep_is_skipped_while_one_runs(repo):
    _plant_running_sweep(hours_old=0.1)
    result = runner.run_sweep(triggered_by_user_id=None)
    assert result["run_id"] is None
    assert result["skipped"] == "sweep already running"
    assert result["proposals_emitted"] == 0


def test_stuck_corpse_run_does_not_block_forever(repo):
    _plant_running_sweep(hours_old=runs.STUCK_RUN_MAX_AGE_HOURS + 1)
    result = runner.run_sweep(triggered_by_user_id=None)
    assert result["run_id"] is not None  # corpse ignored, sweep ran
    assert result["proposals_emitted"] == 1


def test_sequential_sweeps_are_not_rate_limited(repo):
    first = runner.run_sweep(triggered_by_user_id=None)
    assert first["run_id"] is not None
    # The first completed; a manual re-trigger right after runs fine.
    second = runner.run_sweep(triggered_by_user_id=None)
    assert second["run_id"] is not None
