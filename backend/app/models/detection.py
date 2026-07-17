"""HTTP shapes for the Wiki Auto Management detection admin API."""
from __future__ import annotations

from typing import Literal

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


class ProposalView(BaseModel):
    """One ``change_proposals`` row for the pending-cleanups queue."""

    id: int
    op: str
    status: str
    source_paths: list[str]
    target_paths: list[str]
    summary: str
    created_via: str
    run_id: str | None
    created_at: str


class ProposalsResponse(BaseModel):
    proposals: list[ProposalView]


class ProposalActionResponse(BaseModel):
    """Result of approving/rejecting a proposal. On approve, execution is
    enqueued on the detection queue, so ``status`` reflects the decision, not
    the applied state."""

    status: Literal["approved", "rejected"]
