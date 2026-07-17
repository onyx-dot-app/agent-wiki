"""Executor for approved change proposals.

Detection only *emits* proposals; this is the only place that acts on one.
Phase 1 is human-approved only (delegation/auto-apply is Phase 2). On approval
a proposal is handed here (via the detection queue) to apply the structural
change and migrate path-keyed state, then mark itself ``applied``.

``delete_empty_folder`` is the first — and today only — op. It executes as a
**trash-move** (soft delete): the folder is moved into ``.trash/`` exactly like
`DELETE /wiki/file`, so `after_doc_trashed` re-points the folder's ACL/policy
row and tombstones its doc id, and the delete stays losslessly restorable —
honoring the PRD's "no information is ever deleted" invariant.

Re-validation (PRD): a stale proposal never executes. For an empty-folder
delete the meaningful precondition is that the folder is *still empty* — a page
added since detection means the proposal no longer applies, so it goes stale
rather than deleting content.
"""
from __future__ import annotations

import logging
from typing import Any

from app.auth import users
from app.models.wiki import PathMove
from app.wiki import change_proposals, git, notify, trash
from app.wiki.change_proposals import ProposalOp, ProposalStatus

log = logging.getLogger(__name__)


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
    concurrent reject/expire/apply already moved it on."""
    p = change_proposals.get(proposal_id)
    if p is None:
        log.warning("execute: proposal %s is gone", proposal_id)
        return
    if p["status"] != ProposalStatus.APPROVED.value:
        log.info(
            "execute: proposal %s not approved (%s) — skip", proposal_id, p["status"]
        )
        return
    op = p["op"]
    if op == ProposalOp.DELETE_EMPTY_FOLDER.value:
        _execute_delete_empty_folder(p)
        return
    # Only delete_empty_folder has a producer today; other ops are added here
    # alongside their detectors.
    raise ValueError(f"no executor for op {op!r}")


def _execute_delete_empty_folder(p: dict[str, Any]) -> None:
    proposal_id = p["id"]
    folder = p["source_paths"][0]

    if not _folder_still_empty(folder):
        change_proposals.mark_stale(
            proposal_id, reason=f"{folder!r} is no longer an empty folder"
        )
        log.info("execute: proposal %s stale — %s not empty", proposal_id, folder)
        return

    author = _git_author(p["acting_user_id"])
    dest = trash.trash_location(trash.new_trash_id(), folder)
    sha, moves = git.move_path(
        folder, dest, trash.trash_commit_message(folder), author=author
    )
    notify.after_doc_trashed(
        moves, sha, author, root_move=PathMove(old=folder, new=dest)
    )
    change_proposals.mark_applied(proposal_id, applied_sha=sha)
    log.info(
        "execute: proposal %s applied — trashed empty folder %s (sha=%s)",
        proposal_id,
        folder,
        sha[:8],
    )
