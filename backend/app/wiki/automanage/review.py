"""Proposal review coordination — the seam between an approval *decision* and
*execution*.

Both approval sources funnel through here, but their executions ride
**different queues** by latency tier so a human decision never waits on batch
work:

- **human review** — the approve/reject endpoints (`app/api/automanage.py`);
  execution goes on the *nearline* queue (a human is waiting).
- **AI-managed auto-approval** — `ai_management_allowed` scopes need no human,
  so the runner `auto_approve`s and executes on the *offline* queue, alongside
  the sweeps that produced the proposal.

The status transition and the queue differ per source; both are factored out
here so neither path re-implements them and the API router stays thin. Tasks are
imported lazily so this domain module doesn't hard-depend on the tasks layer at
import time (tasks import automanage, not the reverse).
"""
from __future__ import annotations

import logging

from app.wiki import change_proposals
from app.wiki.automanage import executor, settings
from app.wiki.change_proposals import ProposalStatus

log = logging.getLogger(__name__)


def _executable(proposal_id: int) -> bool:
    """Refuse to approve what the executor can't apply. A detector shipping
    ahead of its op's executor (or a bad manual insert) must dead-end here —
    an approved-but-unexecutable proposal would either crash the execute task
    or, on the AI path, auto-approve something that can never run."""
    p = change_proposals.get(proposal_id)
    if p is None:
        return False
    if p["op"] not in executor.SUPPORTED_OPS:
        log.error(
            "review: proposal %s has op %r with no executor — refusing approval",
            proposal_id,
            p["op"],
        )
        return False
    return True


def _dispatch_human_execution(proposal_id: int) -> None:
    """Enqueue a human-approved execution on the *nearline* queue — it applies
    promptly, never behind an in-flight sweep or AI auto-apply batch."""
    from app.tasks.automanage import execute_approved_proposal  # noqa: PLC0415

    execute_approved_proposal(proposal_id)


def _dispatch_ai_execution(proposal_id: int) -> None:
    """Enqueue an AI-auto-approved execution on the *offline* queue, alongside
    the sweeps — batch, nobody waits."""
    from app.tasks.automanage import execute_auto_approved_proposal  # noqa: PLC0415

    execute_auto_approved_proposal(proposal_id)


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
    if not _executable(proposal_id):
        return False
    transitioned = change_proposals.approve(proposal_id, user_id=user_id)
    if not _actionable_after(proposal_id, transitioned):
        return False
    _dispatch_human_execution(proposal_id)
    return True


def auto_approve(proposal_id: int, *, acting_user_id: str) -> bool:
    """AI-managed auto-approval (no human): ``pending → approved`` with no
    reviewer, then execute — the same execution path as human approval. For
    scopes with ``ai_management_allowed`` effective. Idempotent on an
    already-approved proposal. Returns False if missing or terminal, or if Auto
    Organize is disabled."""
    if not settings.is_enabled():
        return False
    if not _executable(proposal_id):
        return False
    transitioned = change_proposals.auto_approve(
        proposal_id, acting_user_id=acting_user_id
    )
    if not _actionable_after(proposal_id, transitioned):
        return False
    _dispatch_ai_execution(proposal_id)
    return True


def reject(proposal_id: int, *, user_id: str, reason: str | None = None) -> bool:
    """Reject a pending proposal (durable do-not-propose). No execution.
    Returns False if it wasn't pending, or if Auto Organize is disabled (pending
    proposals are frozen while off)."""
    if not settings.is_enabled():
        return False
    return change_proposals.reject(proposal_id, user_id=user_id, reason=reason)
