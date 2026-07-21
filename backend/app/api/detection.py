"""Admin API for Wiki Auto Management detection — trigger a sweep, read run history.

Thin HTTP layer: enqueue the sweep task and read the ``detection_runs`` ledger.
All detection logic lives in ``app/wiki/automanage/``. Admin-only — a sweep
reads across the whole wiki.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from app.auth import User, require_can
from app.auth.deps import require_admin, require_user
from app.models.detection import (
    DetectionRunView,
    DetectionSettingsUpdate,
    DetectionSettingsView,
    ProposalActionResponse,
    ProposalsResponse,
    ProposalView,
    RunsResponse,
    SweepTriggerResponse,
)
from app.tasks.detection import run_detection_sweep
from app.wiki import acl
from app.wiki.automanage import review, runs, settings
from app.wiki.change_proposals import (
    ProposalStatus,
    get as get_proposal,
    list_by_status,
)

router = APIRouter()


def _can_see(user: User, proposal: dict[str, Any]) -> bool:
    """A proposal is visible only if the user can read every path it touches —
    so its existence (and the paths in it) never leaks a restricted scope."""
    paths = list(proposal["source_paths"]) + list(proposal["target_paths"])
    return all(acl.can(user.id, user.is_admin, "read", p) for p in paths)


@router.get("/settings", response_model=DetectionSettingsView)
def get_settings(user: User = Depends(require_admin)) -> DetectionSettingsView:
    """The org-wide Auto Organize settings (kill switch + sweep schedule)."""
    s = settings.get()
    return DetectionSettingsView(
        enabled=s.enabled, schedule=s.schedule, updated_at=s.updated_at
    )


@router.put("/settings", response_model=DetectionSettingsView)
def update_settings(
    req: DetectionSettingsUpdate, user: User = Depends(require_admin)
) -> DetectionSettingsView:
    """Update the Auto Organize settings. Turning ``enabled`` off makes the
    whole feature inert (no detection, no proposals, no auto-apply; pending
    proposals frozen) — per-page policies are untouched. ``schedule`` drives the
    recurring sweep (off / daily / weekly)."""
    s = settings.update(
        enabled=req.enabled, schedule=req.schedule, updated_by_user_id=user.id
    )
    return DetectionSettingsView(
        enabled=s.enabled, schedule=s.schedule, updated_at=s.updated_at
    )


@router.post("/sweep", response_model=SweepTriggerResponse, status_code=202)
def trigger_sweep(user: User = Depends(require_admin)) -> SweepTriggerResponse:
    """Kick off a whole-space detection sweep on the detection queue. 409 if
    Auto Organize is disabled."""
    if not settings.is_enabled():
        raise HTTPException(status_code=409, detail="Auto Organize is disabled")
    run_detection_sweep(user.id)
    return SweepTriggerResponse()


@router.get("/runs", response_model=RunsResponse)
def list_runs(user: User = Depends(require_admin)) -> RunsResponse:
    """Most recent detection runs first — the admin sweep history."""
    return RunsResponse(runs=[DetectionRunView(**r) for r in runs.list_recent()])


@router.get("/proposals", response_model=ProposalsResponse)
def list_proposals(user: User = Depends(require_user)) -> ProposalsResponse:
    """Pending cleanup proposals the caller may review — filtered to those whose
    every path the caller can read (an unreadable path would leak its scope).

    Fetches all pending (``limit=None``) before ACL-filtering: the pending queue
    is a bounded working set (drained by review + TTL expiry, capped per sweep),
    and a hard row cap *before* the filter would silently hide a caller's
    readable proposals sitting past it. If the pending set ever grows large,
    push the visibility check into SQL and paginate."""
    pending = list_by_status(ProposalStatus.PENDING, limit=None)
    visible = [p for p in pending if _can_see(user, p)]
    return ProposalsResponse(proposals=[ProposalView(**p) for p in visible])


@router.get("/proposals/{proposal_id}", response_model=ProposalView)
def get_one_proposal(
    proposal_id: int, user: User = Depends(require_user)
) -> ProposalView:
    """A single proposal preview. 404 if missing; 403 if the caller can't read
    every path it touches."""
    proposal = get_proposal(proposal_id)
    if proposal is None:
        raise HTTPException(status_code=404, detail="proposal not found")
    if not _can_see(user, proposal):
        raise HTTPException(status_code=403, detail="not permitted")
    return ProposalView(**proposal)


def _require_writable(proposal_id: int, user: User) -> None:
    """Require the caller can *write* every path the proposal touches — they
    become the acting user, so the change must fit their permissions (PRD:
    executed on behalf of someone who could do it by hand). Side-effect only:
    raises 404 if missing, 403 (via require_can) if not covered."""
    proposal = get_proposal(proposal_id)
    if proposal is None:
        raise HTTPException(status_code=404, detail="proposal not found")
    for path in list(proposal["source_paths"]) + list(proposal["target_paths"]):
        require_can("write", path, user)


@router.post("/proposals/{proposal_id}/approve", response_model=ProposalActionResponse)
def approve_one(
    proposal_id: int, user: User = Depends(require_user)
) -> ProposalActionResponse:
    """Approve a pending proposal and enqueue its execution. The approver
    becomes the acting user. 409 if it isn't pending (already actioned)."""
    _require_writable(proposal_id, user)
    if not review.approve(proposal_id, user_id=user.id):
        raise HTTPException(status_code=409, detail="proposal is not pending")
    return ProposalActionResponse(status="approved")


@router.post("/proposals/{proposal_id}/reject", response_model=ProposalActionResponse)
def reject_one(
    proposal_id: int, user: User = Depends(require_user)
) -> ProposalActionResponse:
    """Reject a pending proposal (durable do-not-propose). 409 if not pending."""
    _require_writable(proposal_id, user)
    if not review.reject(proposal_id, user_id=user.id):
        raise HTTPException(status_code=409, detail="proposal is not pending")
    return ProposalActionResponse(status="rejected")
