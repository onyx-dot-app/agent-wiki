"""Executor for approved change proposals.

Detection only *emits* proposals; this is the only place that acts on one.
Two execution paths:

- ``delete_empty_folder`` keeps its **deterministic** branch: trash-move
  exactly like `DELETE /wiki/file`, losslessly restorable, re-validated
  (still empty, else stale).
- Every other allowed op goes through the **agentic applier**
  (``agentic.apply_proposal``): the LLM applies the approved intent against
  current wiki state with bounded tools. The rails live here — pre-gates
  (kill switch, status, audience-fingerprint drift → stale), and a post-run
  **scope check**: the git diff since the pre-run HEAD may touch only the
  proposal's paths (or their trash locations); a violating run is additively
  reverted and the proposal marked stale with the reason.

Re-validation (PRD): a stale proposal never executes. Audience drift always
stales (who consented changed); trivial *content* drift is the agentic
applier's to absorb — intent-level consent.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from app.auth import users
from app.db.models import Event
from app.db.session import session
from app.models.wiki import ChangeKind, PathMove
from app.wiki import change_proposals, doc_ids, git, notify, trash
from app.llm.agents import automanage_apply
from app.wiki.automanage import fingerprint, settings
from app.wiki.automanage.detectors import DETECTORS_BY_NAME
from app.wiki.change_proposals import ProposalOp, ProposalStatus
from app.wiki.filesystem import TRASH_PREFIX
from app.tracing import trace_flow

log = logging.getLogger(__name__)

# Activity-feed event for an *auto-applied* cleanup (AI-managed scope, no human
# reviewer). The `automanage.` prefix is what `GET /events` matches to give
# admins the space-wide audit trail — keep new automanage kinds under it.
EVENT_AUTOMANAGE_APPLIED = "automanage.applied"

# The ops detectors may emit and reviewers may approve — a *policy allowlist*
# (the agentic applier can apply any op the proposal describes; this is the
# gate on which proposals we let exist). The emit/approve layers check it, so
# widening it is a deliberate one-line decision per op.
SUPPORTED_OPS: frozenset[str] = frozenset(
    {ProposalOp.DELETE_EMPTY_FOLDER.value, ProposalOp.MERGE.value}
)


def _git_author(user_id: str | None) -> str | None:
    """`name <email>` for commit attribution, or None for the default identity."""
    if user_id is None:
        return None
    u = users.get_by_id(user_id)
    if u is None:
        return None
    return f"{u.get('name') or u['email']} <{u['email']}>"


def _folder_still_empty(folder: str) -> bool:
    """True if ``folder`` still exists and holds only ``.gitkeep`` markers (no
    ``.md`` page, no other tracked file). Git tracks files, not dirs, so an
    absent folder yields no tracked files → not empty (nothing to delete)."""
    under = list(git.list_paths(folder))
    return bool(under) and all(p.rsplit("/", 1)[-1] == ".gitkeep" for p in under)


def execute(proposal_id: int) -> None:
    """Apply an approved proposal. No-op (logged) if it isn't ``approved`` — a
    concurrent reject/expire/apply already moved it on — or if Auto Organize is
    disabled (approved proposals are frozen while off; re-enabling resumes)."""
    if not settings.is_enabled():
        log.info("execute: Auto Organize disabled — proposal %s frozen", proposal_id)
        return
    p = change_proposals.get(proposal_id)
    if p is None:
        log.warning("execute: proposal %s is gone", proposal_id)
        return
    if p["status"] != ProposalStatus.APPROVED.value:
        log.info(
            "execute: proposal %s not approved (%s) — skip", proposal_id, p["status"]
        )
        return
    # Audience drift always stales: the fingerprint identifies *who consented*
    # (readers/writers of every touched path); if it moved since emit, the
    # approval no longer covers the world being changed. Content drift, by
    # contrast, is the agentic applier's to absorb. Unstamped rows (pre-
    # fingerprint history) skip the check.
    stamped = p.get("acl_fingerprint_before")
    if stamped is not None:
        current = fingerprint.combined_fingerprint(
            p["source_paths"] + p["target_paths"]
        )
        if current != stamped:
            change_proposals.mark_stale(
                proposal_id,
                reason="permissions of the affected paths changed since this "
                "was proposed",
            )
            log.info(
                "execute: proposal %s stale — audience fingerprint drifted",
                proposal_id,
            )
            return
    # Premise re-validation, dispatched to the detector that authored the
    # proposal: approval can be days old, and only the author knows what
    # "still valid" means (still empty; still byte-identical; ...). Premise-
    # based rather than sha-based on purpose — an edit that doesn't break the
    # premise (the same fix applied to both copies of a duplicate) keeps the
    # proposal valid. Rows without a detector (predate the column, or created
    # outside the detector pipeline) skip this gate; a *stamped* row whose
    # detector is unknown (renamed/removed, mixed-version worker) fails
    # closed — its premise can't be re-validated, so it must not execute.
    detector_name = p.get("detector")
    if detector_name is not None:
        detector = DETECTORS_BY_NAME.get(detector_name)
        if detector is None:
            change_proposals.mark_stale(
                proposal_id,
                reason=f"authoring detector {detector_name!r} is unknown — "
                "premise cannot be re-validated",
            )
            log.error(
                "execute: proposal %s stale — unknown detector %r",
                proposal_id,
                detector_name,
            )
            return
        invalid = detector.validate(p)
        if invalid is not None:
            change_proposals.mark_stale(proposal_id, reason=invalid)
            log.info(
                "execute: proposal %s stale — premise no longer holds (%s)",
                proposal_id,
                invalid,
            )
            return
    op = p["op"]
    if op == ProposalOp.DELETE_EMPTY_FOLDER.value:
        _execute_delete_empty_folder(p)
        return
    _execute_agentic(p)


def _scope_violations(base_sha: str, allowed: frozenset[str]) -> list[str]:
    """Paths the run touched outside the proposal's scope. A path is in scope
    when it is one of the proposal's paths, or that path's location inside a
    trash entry (`.trash/<id>/<path>` — trash-moves report both sides)."""
    head = git.head_sha()
    if head is None or head == base_sha:
        return []
    out: list[str] = []
    for touched in git.changed_paths_between(base_sha, head):
        candidate = touched
        if touched.startswith(TRASH_PREFIX):
            # strip `.trash/<id>/`
            parts = touched.split("/", 2)
            candidate = parts[2] if len(parts) == 3 else touched
        if candidate not in allowed:
            out.append(touched)
    return out


def _reconverge_after_revert(
    allowed: frozenset[str], reverted_sha: str, author: str | None
) -> None:
    """Re-converge path-keyed metadata after an additive revert.

    The revert restored file *content*; metadata mutated through the run's
    lifecycle hooks (id tombstones/forwards, search index) needs pulling back
    for the proposal's paths: restored ids re-bind (clearing any forward) and
    live pages re-index. ACL/policy rows re-pointed toward now-removed trash
    copies are left as orphans — the known-debris class the move machinery
    already heals on collision."""
    live = set(git.list_paths())
    doc_ids.on_restored([path for path in sorted(allowed) if path in live])
    for path in sorted(allowed):
        if path in live and path.endswith(".md"):
            notify.after_doc_write(
                path, reverted_sha, ChangeKind.EDIT, author
            )


def _execute_agentic(p: dict[str, Any]) -> None:
    """Apply via the LLM, then judge the run mechanically: scope violations or
    a failed run revert additively and stale the proposal with the reason."""
    proposal_id = p["id"]
    author = _git_author(p["acting_user_id"])
    allowed = frozenset(p["source_paths"] + p["target_paths"])
    # Durable handles + pre-run anchor, both captured before anything mutates.
    path_ids = {path: doc_ids.get_or_mint(path) for path in sorted(allowed)}
    base_sha = git.head_sha()
    if base_sha is None:
        change_proposals.mark_stale(proposal_id, reason="empty wiki repository")
        return

    with trace_flow("automanage.agentic_execute", proposal_id=proposal_id, op=p["op"]):
        outcome = automanage_apply.apply_proposal(p, author=author)

    violations = _scope_violations(base_sha, allowed)
    if violations or not outcome.ok:
        reverted = git.revert_to(
            base_sha,
            f"automanage: revert rejected execution of proposal {proposal_id}",
            author=author,
        )
        if reverted is not None:
            _reconverge_after_revert(allowed, reverted, author)
        reason = (
            f"execution touched out-of-scope paths: {violations}"
            if violations
            else f"execution did not complete: {outcome.detail}"
        )
        change_proposals.mark_stale(proposal_id, reason=reason)
        log.error(
            "execute: proposal %s rejected (%s)%s",
            proposal_id,
            reason,
            " — reverted" if reverted else "",
        )
        return

    head = git.head_sha()
    assert head is not None  # base_sha existed, so HEAD does
    event_target = (p["target_paths"] or p["source_paths"])[0]
    _finalize_applied(p, applied_sha=head, path_ids=path_ids, event_target=event_target)
    log.info(
        "execute: proposal %s applied agentically (%s) — %s",
        proposal_id,
        p["op"],
        outcome.detail,
    )


def _execute_delete_empty_folder(p: dict[str, Any]) -> None:
    proposal_id = p["id"]
    folder = p["source_paths"][0]

    if not _folder_still_empty(folder):
        change_proposals.mark_stale(
            proposal_id, reason=f"{folder!r} is no longer an empty folder"
        )
        log.info("execute: proposal %s stale — %s not empty", proposal_id, folder)
        return

    # Capture the folder's stable id *before* the trash-move tombstones it, so
    # the activity event carries a durable handle (the id keeps resolving to the
    # tombstone via `doc_ids.get`, even though the path is now gone).
    path_ids = {folder: doc_ids.get_or_mint(folder)}

    author = _git_author(p["acting_user_id"])
    dest = trash.trash_location(trash.new_trash_id(), folder)
    sha, moves = git.move_path(
        folder, dest, trash.trash_commit_message(folder), author=author
    )
    notify.after_doc_trashed(
        moves, sha, author, root_move=PathMove(old=folder, new=dest)
    )
    # The event targets the trash destination: `after_doc_trashed` just
    # re-pointed the folder's owner row there, and `/events` matches owners by
    # exact target path — so the deleted folder's owner sees the event. The
    # payload keeps the original path for display.
    _finalize_applied(p, applied_sha=sha, path_ids=path_ids, event_target=dest)
    log.info(
        "execute: proposal %s applied — trashed empty folder %s (sha=%s)",
        proposal_id,
        folder,
        sha[:8],
    )


def _finalize_applied(
    p: dict[str, Any],
    *,
    applied_sha: str,
    path_ids: dict[str, str],
    event_target: str,
) -> None:
    """Mark the proposal ``applied`` and, when it was auto-applied (no human
    reviewer), record an activity event. A human-approved apply is already
    visible to the approver (they watched the outcome in the review banner), so
    only the silent AI-managed path needs the audit trail. ``path_ids`` maps the
    affected paths to their stable doc ids (captured before mutation);
    ``event_target`` is the path whose *current* owner row should match (the op
    decides — for a trash-move that's the trash destination, where the owner
    row now lives)."""
    # `mark_applied` is a conditional approved→applied transition: it returns
    # False if a concurrent change (reject/expire/stale) already moved the
    # proposal off `approved`. Only emit the event when the transition actually
    # happened, so the audit feed can't disagree with the persisted status.
    applied = change_proposals.mark_applied(p["id"], applied_sha=applied_sha)
    if applied and p["reviewed_by_user_id"] is None:
        _record_applied_event(p, applied_sha, path_ids, event_target)


def _record_applied_event(
    p: dict[str, Any], applied_sha: str, path_ids: dict[str, str], target: str
) -> None:
    """Best-effort: the change already happened, so a feed write that fails must
    not fail the apply. ``/events`` matches owners by exact ``target`` path at
    query time, so ``target`` must be where the affected item's owner row lives
    *after* the op — the trash destination for a delete. The item's owner sees
    the event; admins see every ``automanage.*`` event regardless. ``path_ids``
    gives the UI a durable handle for linking (paths churn; the id resolves
    even after the item is trashed); display paths come from ``source_paths``,
    never from ``target``."""
    try:
        with session() as s:
            s.add(
                Event(
                    kind=EVENT_AUTOMANAGE_APPLIED,
                    actor=p["acting_user_id"],
                    target=target,
                    payload_json=json.dumps(
                        {
                            "op": p["op"],
                            "source_paths": p["source_paths"],
                            "target_paths": p["target_paths"],
                            "path_ids": path_ids,
                            "applied_sha": applied_sha,
                        }
                    ),
                )
            )
    except Exception:
        log.exception(
            "execute: failed to record %s event for proposal %s",
            EVENT_AUTOMANAGE_APPLIED,
            p["id"],
        )
