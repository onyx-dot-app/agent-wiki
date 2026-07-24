"""Detection runner — the substrate the detectors plug into.

A trigger calls in here; the runner builds the ``Scope``, runs every
applicable detector, applies the cross-cutting guardrails, attaches the drift
anchors, persists surviving drafts as ``pending`` ``change_proposals``, and
records the ``detection_runs`` row. Detectors stay pure (``scope → drafts``);
everything stateful lives here so it's inherited uniformly.

Emit-only: this never mutates the wiki. Execution happens later, on approval.

Guardrails applied to every draft:
- **Do-not-re-propose** — the dedup component (`automanage/dedup.py`) matches
  every draft against the ledger by finding identity: live rows carry, rejected
  rows suppress forever, stale/expired rows revive in place.
- **Forbidden scopes** — drop a draft touching any path whose effective
  ``ai_management_allowed`` is explicitly ``False`` (the do-not-manage marker).
- **Per-run cap** — stop emitting past ``MAX_PROPOSALS_PER_RUN`` and log the
  truncation, so one run can't flood the queue.

Permission-fingerprint partitioning: detectors marked ``pairs_paths`` receive
one same-audience bucket of pages at a time (``_partition_by_audience``), so
pages with different audiences are never named together in one proposal.
Single-path detectors (empty-folder) see the whole scope. Every emitted
proposal is stamped with the combined audience fingerprint of its path-set.
"""
from __future__ import annotations

import logging
from typing import Any

from app.auth.users import AI_USER_ID
from app.wiki import git, update_policy
from app.wiki.automanage import dedup, executor, fingerprint, review, runs, settings
from app.wiki.automanage.detectors import DETECTORS
from app.wiki.automanage.detectors.base import ProposalDraft, Scope, TriggerKind
from app.wiki.change_proposals import (
    ProposalCreatedVia,
)
from app.wiki.change_proposals import (
    create as create_proposal,
)
from app.wiki.change_proposals import (
    get as get_proposal,
)
from app.wiki.change_proposals import (
    revive as change_proposals_revive,
)

log = logging.getLogger(__name__)

# Ceiling on proposals emitted by a single run — backpressure so one sweep
# can't flood the pending-cleanups queue. Excess is logged, not silently
# dropped.
MAX_PROPOSALS_PER_RUN = 50

_CREATED_VIA = {
    TriggerKind.SWEEP: ProposalCreatedVia.SWEEP,
    TriggerKind.ON_CREATE: ProposalCreatedVia.ON_CREATE,
}


def _resolve_management(drafts: list[ProposalDraft]) -> dict[str, bool | None]:
    """Effective ``ai_management_allowed`` tri-state for every path across
    ``drafts``, resolved in one query. Per draft: any path False → skip
    (hands-off); all paths True → auto-manage (auto-approve + execute as the AI
    system user); otherwise → pending for human review."""
    paths = {p for d in drafts for p in d.source_paths + d.target_paths}
    return update_policy.resolve_ai_management_for_paths(paths)


def _base_shas(
    source_paths: list[str], target_paths: list[str]
) -> dict[str, str] | None:
    """Drift anchors: every affected path that has history gets one. Paths
    without history are reserved/new names (a rename option, a move
    destination) — nothing to anchor. A draft none of whose paths can be
    anchored has no drift protection at all → skip it."""
    shas: dict[str, str] = {}
    for p in source_paths + target_paths:
        meta = git.last_commit_meta_for_path(p)
        if meta is not None:
            shas[p] = meta[0]
    return shas or None


def _partition_by_audience(scope: Scope) -> list[Scope]:
    """Split a scope into same-audience sub-scopes for pairing detectors.

    Only ``.md`` pages are fingerprinted and bucketed (pairing techniques
    compare page content; markers like ``.gitkeep`` never pair). Buckets of a
    single page are dropped — nothing to pair. Most wikis are default-public,
    so this usually returns one big bucket and the partition costs one batched
    fingerprint pass."""
    pages = [p for p in scope.paths if p.endswith(".md")]
    if not pages:
        return []
    fps = fingerprint.fingerprints_for_paths(pages)
    by_fp: dict[str, list[str]] = {}
    for path in pages:
        by_fp.setdefault(fps[path], []).append(path)
    return [
        scope.model_copy(update={"paths": tuple(sorted(group))})
        for _, group in sorted(by_fp.items())
        if len(group) > 1
    ]


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

    # Master kill switch, re-checked at the one chokepoint every sweep/detection
    # flow passes through — so a disable that lands after a caller's own check
    # (e.g. a scheduled sweep between reading settings and starting) still keeps
    # this run from emitting any proposals. The action layer (auto_approve /
    # approve / executor) re-checks too, so nothing emitted in the residual race
    # window can be applied while disabled.
    if not settings.is_enabled():
        log.info("detection: Auto Organize disabled — %s run skipped", trigger.value)
        return {"run_id": None, "paths_scanned": 0, "proposals_emitted": 0}

    # Sweeps are singleton: the run row doubles as the slot (a partial unique
    # index allows at most one running sweep), so acquisition is atomic — no
    # check-then-insert window even for direct callers or multiple consumers.
    if trigger is TriggerKind.SWEEP:
        acquired = runs.try_start_sweep(triggered_by_user_id=triggered_by_user_id)
        if acquired is None:
            log.info("detection: sweep already running — skipped")
            return {
                "run_id": None,
                "paths_scanned": 0,
                "proposals_emitted": 0,
                "skipped": "sweep already running",
            }
        run_id = acquired
    else:
        run_id = runs.start(
            trigger=trigger, triggered_by_user_id=triggered_by_user_id
        )
    try:
        scope = Scope(trigger=trigger, paths=tuple(paths), run_id=run_id)
        deduper = dedup.Deduper(paths)
        emitted = 0
        capped = False
        # Pairing detectors compare pages against each other; feeding them one
        # permission bucket at a time means pages with different audiences are
        # never mentioned in one proposal (which alone would leak a restricted
        # page's existence to whoever sees the review surface). Buckets are
        # computed once per run and only when a pairing detector will run.
        buckets: list[Scope] | None = None
        for detector in DETECTORS:
            if capped:
                break
            if not detector.applicable(trigger):
                continue
            if getattr(detector, "pairs_paths", False):
                if buckets is None:
                    buckets = _partition_by_audience(scope)
                drafts = [d for sub in buckets for d in detector.detect(sub)]
            else:
                drafts = detector.detect(scope)
            # One policy query per detector, not one per path per draft.
            mgmt = _resolve_management(drafts)
            for draft in drafts:
                draft_paths = draft.source_paths + draft.target_paths
                # Emit safety: never persist a draft the executor can't apply —
                # a detector landing ahead of its op's executor degrades to this
                # loud skip instead of filling the queue with proposals that
                # dead-end at approval (review re-checks the same set).
                if draft.op.value not in executor.SUPPORTED_OPS:
                    log.error(
                        "detection run %s: detector %s emitted op %r with no "
                        "executor — draft skipped (%s)",
                        run_id,
                        detector.name,
                        draft.op.value,
                        draft.summary,
                    )
                    continue
                if any(mgmt.get(p) is False for p in draft_paths):
                    continue  # explicitly hands-off scope
                # Dedup is pure identity: a new premise on recently
                # rejected pages CREATEs immediately by design. The
                # post-rejection cooldown is a *selection*-step concern
                # (persisted but unselectable — see the Dedup design page)
                # and lands with the reconciliation work, not here.
                decision = deduper.decide(detector.name, draft)
                if decision.action not in (
                    dedup.DedupAction.CREATE,
                    dedup.DedupAction.REVIVE,
                ):
                    continue  # carried, or rejected-forever
                if emitted >= MAX_PROPOSALS_PER_RUN:
                    log.warning(
                        "detection run %s hit per-run cap (%d); "
                        "remaining drafts deferred to next run",
                        run_id,
                        MAX_PROPOSALS_PER_RUN,
                    )
                    capped = True
                    break
                base_shas = _base_shas(draft.source_paths, draft.target_paths)
                if base_shas is None:
                    continue
                # Audience snapshot at emit time — staleness re-checks can
                # notice a permission change (e.g. group-membership drift)
                # between proposal and execution.
                acl_fp = fingerprint.combined_fingerprint(draft_paths)
                resolution = deduper.resolution(draft)
                if decision.action is dedup.DedupAction.REVIVE:
                    if decision.existing_id is None:
                        # Unreachable: DedupDecision enforces this at
                        # construction. Skip loudly rather than crash a run.
                        log.error(
                            "detection run %s: REVIVE decision without a row "
                            "(%s) — skipped",
                            run_id,
                            decision.dedup_key,
                        )
                        continue
                    if not change_proposals_revive(
                        decision.existing_id,
                        source_paths=draft.source_paths,
                        target_paths=draft.target_paths,
                        base_shas=base_shas,
                        summary=draft.summary,
                        instruction=draft.instruction,
                        proposed_bodies=draft.proposed_bodies,
                        run_id=run_id,
                        acl_fingerprint_before=acl_fp,
                        doc_ids=resolution,
                    ):
                        # A race moved the row out of stale/expired since the
                        # decision — whatever won represents the finding now.
                        continue
                    proposal = get_proposal(decision.existing_id)
                    if proposal is None:
                        continue
                    log.info(
                        "detection run %s: revived proposal %s (%s)",
                        run_id,
                        decision.existing_id,
                        decision.dedup_key,
                    )
                else:
                    proposal = create_proposal(
                        op=draft.op,
                        source_paths=draft.source_paths,
                        target_paths=draft.target_paths,
                        base_shas=base_shas,
                        summary=draft.summary,
                        created_via=created_via,
                        detector=detector.name,
                        instruction=draft.instruction,
                        proposed_bodies=draft.proposed_bodies,
                        run_id=run_id,
                        acl_fingerprint_before=acl_fp,
                        dedup_key=decision.dedup_key,
                        doc_ids=resolution,
                    )
                emitted += 1
                # Whole operation inside an AI-managed scope → auto-apply as the
                # AI system user (no human queue). Otherwise it waits for a
                # human in the pending-cleanups queue. The detector must also
                # consent (`auto_approvable`) — probabilistic detectors emit
                # False so their proposals always get a human even in
                # AI-managed scopes.
                if (
                    draft.auto_approvable
                    and draft_paths
                    and all(mgmt.get(p) is True for p in draft_paths)
                ) and not review.auto_approve(
                    proposal["id"], acting_user_id=AI_USER_ID
                ):
                        # Shouldn't happen for a just-created proposal; if a race
                        # transitioned it out of pending, it stays pending (a
                        # human can still action it) — surface the anomaly loudly.
                        log.warning(
                            "detection run %s: auto-approve did not take for "
                            "proposal %s (%s); left pending",
                            run_id,
                            proposal["id"],
                            decision.dedup_key,
                        )
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
    path so folder-subtree detectors (empty-folder) see complete subtrees.

    **Singleton:** while a sweep is already running, another one is skipped —
    two concurrent whole-space scans emit the same drafts into the same dedup
    window and double every detector's cost for nothing. The slot is acquired
    atomically inside ``run_detection`` (``runs.try_start_sweep``); the manual
    trigger stays available (it skips *overlap*, it doesn't rate-limit), and a
    stuck ``running`` corpse row stops blocking after
    ``STUCK_RUN_MAX_AGE_HOURS``.
    """
    return run_detection(
        trigger=TriggerKind.SWEEP,
        triggered_by_user_id=triggered_by_user_id,
        paths=list(git.list_paths()),
    )
