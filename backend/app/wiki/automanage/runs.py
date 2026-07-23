"""Detection-run repo — the ``detection_runs`` ledger.

One row per detection run (sweep or single-page check). The runner stamps a
``running`` row before it detects, then closes it ``completed``/``failed`` with
scan stats. A run's ``id`` is what proposals carry in ``change_proposals.run_id``,
so a run and everything it emitted join on it.

Free functions over ``DetectionRun``; each opens its own session and returns
plain dicts, like ``app/wiki/change_proposals.py``.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.db.models import DetectionRun
from app.db.session import execute_dml, session
from app.wiki.automanage.detectors.base import TriggerKind


class RunStatus(str, Enum):
    """Lifecycle: ``running → completed | failed``. The DB CHECK mirrors these."""

    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")


def _to_dict(row: DetectionRun) -> dict[str, Any]:
    return {
        "id": row.id,
        "trigger": row.trigger,
        "status": row.status,
        "triggered_by_user_id": row.triggered_by_user_id,
        "paths_scanned": row.paths_scanned,
        "proposals_emitted": row.proposals_emitted,
        "error": row.error,
        "started_at": row.started_at,
        "finished_at": row.finished_at,
    }


def start(*, trigger: TriggerKind, triggered_by_user_id: str | None) -> str:
    """Insert a ``running`` run and return its id (the ``run_id`` proposals
    carry). ``triggered_by_user_id`` is the admin for a manual sweep, NULL for
    system/scheduled runs."""
    run_id = uuid.uuid4().hex
    with session() as s:
        s.add(
            DetectionRun(
                id=run_id,
                trigger=trigger.value,
                status=RunStatus.RUNNING.value,
                triggered_by_user_id=triggered_by_user_id,
                started_at=_now(),
            )
        )
    return run_id


def mark_completed(run_id: str, *, paths_scanned: int, proposals_emitted: int) -> None:
    with session() as s:
        touched = execute_dml(
            s,
            update(DetectionRun)
            .where(DetectionRun.id == run_id)
            .values(
                status=RunStatus.COMPLETED.value,
                paths_scanned=paths_scanned,
                proposals_emitted=proposals_emitted,
                finished_at=_now(),
            ),
        )
    if not touched:
        raise ValueError(f"detection run {run_id!r} not found")


def mark_failed(run_id: str, *, error: str) -> None:
    with session() as s:
        touched = execute_dml(
            s,
            update(DetectionRun)
            .where(DetectionRun.id == run_id)
            .values(
                status=RunStatus.FAILED.value,
                error=error,
                finished_at=_now(),
            ),
        )
    if not touched:
        raise ValueError(f"detection run {run_id!r} not found")


# A ``running`` row older than this is a corpse (worker died mid-run without
# marking failure) — it must not block sweeps forever.
STUCK_RUN_MAX_AGE_HOURS = 2


def try_start_sweep(*, triggered_by_user_id: str | None) -> str | None:
    """Atomically acquire the sweep slot: insert the ``running`` row, or
    return None while a sweep is already in flight.

    The slot is the partial unique index ``uq_detection_runs_single_running_
    sweep`` (at most one ``running`` sweep row), so acquisition is a single
    guarded INSERT — no check-then-insert window, safe for direct callers
    and multi-consumer deployments alike, not just the serialized queue.
    Corpse rows — ``running`` but older than ``STUCK_RUN_MAX_AGE_HOURS``
    (a worker died mid-run; any real sweep finishes in minutes) — are failed
    over first so they never hold the slot forever."""
    cutoff = (
        datetime.now(UTC) - timedelta(hours=STUCK_RUN_MAX_AGE_HOURS)
    ).strftime("%Y-%m-%d %H:%M:%S")
    run_id = uuid.uuid4().hex
    with session() as s:
        execute_dml(
            s,
            update(DetectionRun)
            .where(
                DetectionRun.trigger == TriggerKind.SWEEP.value,
                DetectionRun.status == RunStatus.RUNNING.value,
                DetectionRun.started_at < cutoff,
            )
            .values(
                status=RunStatus.FAILED.value,
                error="timed out — assumed dead (worker exited mid-run?)",
                finished_at=_now(),
            ),
        )
        # Targetless DO NOTHING: the only realistic conflict is the partial
        # unique index (a live sweep holds the slot); the uuid4 PK doesn't
        # collide in practice. RETURNING reports the outcome — the id comes
        # back only when this insert won the slot (rowcount is unreliable
        # for INSERT … ON CONFLICT under implicit returning).
        won = s.execute(
            pg_insert(DetectionRun)
            .values(
                id=run_id,
                trigger=TriggerKind.SWEEP.value,
                status=RunStatus.RUNNING.value,
                triggered_by_user_id=triggered_by_user_id,
                started_at=_now(),
            )
            .on_conflict_do_nothing()
            .returning(DetectionRun.id)
        ).first()
    return run_id if won is not None else None


def get(run_id: str) -> dict[str, Any] | None:
    with session() as s:
        row = s.get(DetectionRun, run_id)
        return _to_dict(row) if row is not None else None


def list_recent(*, limit: int = 50) -> list[dict[str, Any]]:
    """Most recent runs first — the admin sweep history."""
    with session() as s:
        rows = s.scalars(
            select(DetectionRun)
            .order_by(DetectionRun.started_at.desc(), DetectionRun.id.desc())
            .limit(limit)
        ).all()
        return [_to_dict(r) for r in rows]
