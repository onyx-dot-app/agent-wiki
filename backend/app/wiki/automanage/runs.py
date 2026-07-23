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
from datetime import UTC, datetime
from enum import Enum
from typing import Any

from sqlalchemy import select, update

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
