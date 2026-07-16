"""Detection runner — the substrate the detectors plug into.

A trigger calls in here; the runner builds the ``Scope``, runs every
applicable detector, applies the cross-cutting guardrails, attaches the drift
anchors, persists surviving drafts as ``pending`` ``change_proposals``, and
records the ``detection_runs`` row. Detectors stay pure (``scope → drafts``);
everything stateful lives here so it's inherited uniformly.

Emit-only: this never mutates the wiki. Execution happens later, on approval.

Guardrails applied to every draft:
- **Do-not-re-propose** — drop a draft whose op+path-set already has a pending,
  approved, applied, or human-rejected proposal (`change_proposals.taken_dedupe_keys`).
- **Forbidden scopes** — drop a draft touching any path whose effective
  ``ai_management_allowed`` is explicitly ``False`` (the do-not-manage marker).
- **Per-run cap** — stop emitting past ``MAX_PROPOSALS_PER_RUN`` and log the
  truncation, so one run can't flood the queue.

Permission-fingerprint partitioning (the pairing-time boundary in the design)
is a duplicate/misplacement concern — the empty-folder detector proposes on a
single path and never pairs across a visibility boundary, so it isn't exercised
yet; the ACL fingerprint on the proposal is left null until that lands.
"""
from __future__ import annotations

import logging
from typing import Any

from app.wiki import git
from app.wiki import update_policy
from app.wiki.automanage import runs
from app.wiki.automanage.detectors import DETECTORS
from app.wiki.automanage.detectors.base import ProposalDraft, Scope, TriggerKind
from app.wiki.change_proposals import (
    ProposalCreatedVia,
    ProposalStatus,
    create as create_proposal,
    taken_dedupe_keys,
)

log = logging.getLogger(__name__)

# Ceiling on proposals emitted by a single run — backpressure so one sweep
# can't flood the pending-cleanups queue. Excess is logged, not silently
# dropped.
MAX_PROPOSALS_PER_RUN = 50

# Statuses that block re-proposing the same op+path-set. expired/stale are
# omitted so a timed-out or drifted proposal can recur.
_BLOCKING_STATUSES = (
    ProposalStatus.PENDING,
    ProposalStatus.APPROVED,
    ProposalStatus.APPLIED,
    ProposalStatus.REJECTED,
)

_CREATED_VIA = {
    TriggerKind.SWEEP: ProposalCreatedVia.SWEEP,
    TriggerKind.ON_CREATE: ProposalCreatedVia.ON_CREATE,
}


def _forbidden_paths(drafts: list[ProposalDraft]) -> set[str]:
    """Paths across ``drafts`` in an explicitly do-not-manage scope (effective
    ``ai_management_allowed`` is False), resolved in a single query."""
    paths = {p for d in drafts for p in d.source_paths + d.target_paths}
    resolved = update_policy.resolve_ai_management_for_paths(paths)
    return {p for p, v in resolved.items() if v is False}


def _base_shas(source_paths: list[str]) -> dict[str, str] | None:
    """Drift anchor per source path: the last commit that touched it. Returns
    None if any source has no history (can't anchor → skip the draft)."""
    shas: dict[str, str] = {}
    for p in source_paths:
        meta = git.last_commit_meta_for_path(p)
        if meta is None:
            return None
        shas[p] = meta[0]
    return shas


def run_detection(
    *, trigger: TriggerKind, triggered_by_user_id: str | None, paths: list[str]
) -> dict[str, Any]:
    """Run every applicable detector over ``paths`` and emit proposals.

    Records a ``detection_runs`` row (running → completed/failed) and returns
    ``{run_id, paths_scanned, proposals_emitted}``. Raises on failure after
    marking the run failed, so the queue records the error too.
    """
    created_via = _CREATED_VIA.get(trigger)
    if created_via is None:
        raise ValueError(f"no change-proposal entry point for trigger {trigger.value!r}")

    run_id = runs.start(trigger=trigger, triggered_by_user_id=triggered_by_user_id)
    try:
        scope = Scope(trigger=trigger, paths=tuple(paths), run_id=run_id)
        taken = taken_dedupe_keys(_BLOCKING_STATUSES)
        emitted = 0
        capped = False
        for detector in DETECTORS:
            if capped:
                break
            if not detector.applicable(trigger):
                continue
            drafts = detector.detect(scope)
            # One policy query per detector, not one per path per draft.
            forbidden = _forbidden_paths(drafts)
            for draft in drafts:
                if draft.dedupe_key in taken:
                    continue
                if any(p in forbidden for p in draft.source_paths + draft.target_paths):
                    continue
                if emitted >= MAX_PROPOSALS_PER_RUN:
                    log.warning(
                        "detection run %s hit per-run cap (%d); "
                        "remaining drafts deferred to next run",
                        run_id,
                        MAX_PROPOSALS_PER_RUN,
                    )
                    capped = True
                    break
                base_shas = _base_shas(draft.source_paths)
                if base_shas is None:
                    continue
                create_proposal(
                    op=draft.op,
                    source_paths=draft.source_paths,
                    target_paths=draft.target_paths,
                    base_shas=base_shas,
                    summary=draft.summary,
                    created_via=created_via,
                    proposed_bodies=draft.proposed_bodies,
                    run_id=run_id,
                )
                taken.add(draft.dedupe_key)
                emitted += 1
        runs.mark_completed(
            run_id, paths_scanned=len(paths), proposals_emitted=emitted
        )
        log.info(
            "detection run %s (%s): scanned %d paths, emitted %d proposals",
            run_id,
            trigger.value,
            len(paths),
            emitted,
        )
    except Exception as e:
        runs.mark_failed(run_id, error=str(e))
        log.exception("detection run %s failed", run_id)
        raise
    return {
        "run_id": run_id,
        "paths_scanned": len(paths),
        "proposals_emitted": emitted,
    }


def run_sweep(*, triggered_by_user_id: str | None) -> dict[str, Any]:
    """Whole-space sweep — the admin-triggered trigger. Scope is every tracked
    path so folder-subtree detectors (empty-folder) see complete subtrees."""
    return run_detection(
        trigger=TriggerKind.SWEEP,
        triggered_by_user_id=triggered_by_user_id,
        paths=list(git.list_paths()),
    )
