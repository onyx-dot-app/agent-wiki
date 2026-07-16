"""HTTP shapes for the Wiki Auto Management detection admin API."""
from __future__ import annotations

from pydantic import BaseModel


class SweepTriggerResponse(BaseModel):
    """Result of asking for a sweep. The sweep runs on the detection queue, so
    the request only enqueues it."""

    status: str = "queued"


class DetectionRunView(BaseModel):
    """One ``detection_runs`` row for the admin sweep history."""

    id: str
    trigger: str
    status: str
    triggered_by_user_id: str | None
    paths_scanned: int
    proposals_emitted: int
    error: str | None
    started_at: str
    finished_at: str | None


class RunsResponse(BaseModel):
    runs: list[DetectionRunView]
