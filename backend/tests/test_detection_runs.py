"""detection_runs repo — the run ledger (inert until the runner lands).

Lifecycle: start (running) → mark_completed | mark_failed, plus get / list.
"""
from __future__ import annotations

from app.wiki.automanage import runs
from app.wiki.automanage.detectors.base import TriggerKind


def test_start_creates_running_run(tmp_db):
    run_id = runs.start(trigger=TriggerKind.SWEEP, triggered_by_user_id=None)
    row = runs.get(run_id)
    assert row is not None
    assert row["status"] == "running"
    assert row["trigger"] == "sweep"
    assert row["triggered_by_user_id"] is None
    assert row["paths_scanned"] == 0
    assert row["proposals_emitted"] == 0
    assert row["finished_at"] is None


def test_mark_completed_records_stats(tmp_db):
    run_id = runs.start(trigger=TriggerKind.SWEEP, triggered_by_user_id=None)
    runs.mark_completed(run_id, paths_scanned=12, proposals_emitted=3)
    row = runs.get(run_id)
    assert row is not None
    assert row["status"] == "completed"
    assert row["paths_scanned"] == 12
    assert row["proposals_emitted"] == 3
    assert row["finished_at"] is not None


def test_mark_failed_records_error(tmp_db):
    run_id = runs.start(trigger=TriggerKind.ON_CREATE, triggered_by_user_id=None)
    runs.mark_failed(run_id, error="boom")
    row = runs.get(run_id)
    assert row is not None
    assert row["status"] == "failed"
    assert row["error"] == "boom"
    assert row["finished_at"] is not None


def test_list_recent_newest_first(tmp_db):
    # Distinct started_at so ordering is deterministic (start() stamps to the
    # second, so two same-second runs wouldn't have a stable order).
    from app.db.models import DetectionRun
    from app.db.session import session

    # `completed` status: two simultaneous *running* sweep rows are outlawed
    # by the partial unique index backing atomic sweep-slot acquisition.
    with session() as s:
        s.add(
            DetectionRun(
                id="older",
                trigger="sweep",
                status="completed",
                started_at="2026-07-16 10:00:00",
            )
        )
        s.add(
            DetectionRun(
                id="newer",
                trigger="sweep",
                status="completed",
                started_at="2026-07-16 11:00:00",
            )
        )
    ids = [r["id"] for r in runs.list_recent()]
    assert ids.index("newer") < ids.index("older")


def test_mark_unknown_run_raises(tmp_db):
    import pytest

    with pytest.raises(ValueError):
        runs.mark_completed("nope", paths_scanned=1, proposals_emitted=0)
    with pytest.raises(ValueError):
        runs.mark_failed("nope", error="x")
