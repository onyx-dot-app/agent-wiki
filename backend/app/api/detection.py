"""Admin API for Wiki Auto Management detection — trigger a sweep, read run history.

Thin HTTP layer: enqueue the sweep task and read the ``detection_runs`` ledger.
All detection logic lives in ``app/wiki/automanage/``. Admin-only — a sweep
reads across the whole wiki.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends

from app.auth import User
from app.auth.deps import require_admin
from app.models.detection import DetectionRunView, RunsResponse, SweepTriggerResponse
from app.tasks.detection import run_detection_sweep
from app.wiki.automanage import runs

router = APIRouter()


@router.post("/sweep", response_model=SweepTriggerResponse, status_code=202)
def trigger_sweep(user: User = Depends(require_admin)) -> SweepTriggerResponse:
    """Kick off a whole-space detection sweep on the detection queue."""
    run_detection_sweep(user.id)
    return SweepTriggerResponse()


@router.get("/runs", response_model=RunsResponse)
def list_runs(user: User = Depends(require_admin)) -> RunsResponse:
    """Most recent detection runs first — the admin sweep history."""
    return RunsResponse(runs=[DetectionRunView(**r) for r in runs.list_recent()])
