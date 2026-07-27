"""HTTP shapes for the Wiki Auto Management detection admin API."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class SweepTriggerResponse(BaseModel):
    """Result of asking for a sweep. The sweep runs on the detection queue, so
    the request only enqueues it."""

    status: str = "queued"


class DetectionSettingsView(BaseModel):
    """Org-wide Auto Organize settings (the kill switch + sweep schedule)."""

    enabled: bool
    schedule: str
    updated_at: str | None


class DetectionSettingsUpdate(BaseModel):
    """Patch for the Auto Organize settings — only the fields provided change."""

    enabled: bool | None = None
    schedule: Literal["off", "daily", "weekly"] | None = None


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
    # When a sweep last asserted this finding against current wiki state —
    # created/revived/carried all stamp it. Backs the card's freshness line.
    last_emitted_at: str | None = None


class ProposalsResponse(BaseModel):
    proposals: list[ProposalView]


class RejectRequest(BaseModel):
    """Optional body for reject. ``dont_ask_again`` also sets the
    do-not-consolidate marker (``ai_management_allowed=False``) on every
    page the proposal touches, so detectors skip them entirely from now
    on — the durable, explicit "never ask about these pages"."""

    dont_ask_again: bool = False


class ProposalActionResponse(BaseModel):
    """Result of actioning a proposal. On approve, execution is enqueued on
    the detection queue, so ``status`` reflects the decision, not the
    applied state."""

    status: Literal["approved", "rejected", "dismissed"]
