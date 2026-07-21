"""Proposal review coordination — the seam between an approval *decision* and
*execution*.

Both approval sources funnel through here so they share one execution path:

- **human review** — the approve/reject endpoints (`app/api/automanage.py`);
- **AI-managed auto-approval** — `ai_management_allowed` scopes need no human
  approval, so the runner can `auto_approve` and execute directly (wired in a
  follow-up).

The only thing that differs between them is the status transition; **enqueuing
execution is the common piece** (`_dispatch_execution`), factored out here so
neither path re-implements it and the API router stays thin.
"""
from __future__ import annotations

from app.wiki import change_proposals
from app.wiki.automanage import settings
from app.wiki.change_proposals import ProposalStatus


def _dispatch_execution(proposal_id: int) -> None:
    """Enqueue execution of an approved proposal on the detection queue — the
    shared step every approval path calls. Imported lazily so this domain
    module doesn't hard-depend on the tasks layer at import time (tasks import
    automanage, not the reverse)."""
    from app.tasks.detection import execute_proposal

    execute_proposal(proposal_id)


def _actionable_after(proposal_id: int, transitioned: bool) -> bool:
    """Whether execution should be (re-)dispatched. True if we just transitioned
    the proposal to ``approved``, OR it is *already* ``approved`` — the latter
    is the recovery case: a prior approval committed the DB transition but its
    enqueue failed (Redis blip / queue full), leaving the proposal stuck. Since
    the executor is idempotent on status, re-dispatching is safe, so a retried
    approve heals it instead of dead-ending at 409. Missing or terminal
    (applied/rejected/stale/expired) → not actionable."""
    if transitioned:
        return True
    p = change_proposals.get(proposal_id)
    return p is not None and p["status"] == ProposalStatus.APPROVED.value


def approve(proposal_id: int, *, user_id: str) -> bool:
    """Human approval: transition ``pending → approved`` (the approver becomes
    the acting user), then execute. Idempotent on an already-approved proposal
    (re-dispatches). Returns False if the proposal is missing or terminal, or
    if Auto Organize is disabled (proposals are frozen while off)."""
    if not settings.is_enabled():
        return False
    transitioned = change_proposals.approve(proposal_id, user_id=user_id)
    if not _actionable_after(proposal_id, transitioned):
        return False
    _dispatch_execution(proposal_id)
    return True


def auto_approve(proposal_id: int, *, acting_user_id: str) -> bool:
    """AI-managed auto-approval (no human): ``pending → approved`` with no
    reviewer, then execute — the same execution path as human approval. For
    scopes with ``ai_management_allowed`` effective. Idempotent on an
    already-approved proposal. Returns False if missing or terminal, or if Auto
    Organize is disabled."""
    if not settings.is_enabled():
        return False
    transitioned = change_proposals.auto_approve(
        proposal_id, acting_user_id=acting_user_id
    )
    if not _actionable_after(proposal_id, transitioned):
        return False
    _dispatch_execution(proposal_id)
    return True


def reject(proposal_id: int, *, user_id: str, reason: str | None = None) -> bool:
    """Reject a pending proposal (durable do-not-propose). No execution.
    Returns False if it wasn't pending, or if Auto Organize is disabled (pending
    proposals are frozen while off)."""
    if not settings.is_enabled():
        return False
    return change_proposals.reject(proposal_id, user_id=user_id, reason=reason)
