"""Live-rebase: fold an inbound agent/ingest commit into an open co-edit session.

When a page under a live session is committed to git out of band (an agent, an
ingest job, or a classic ``PUT /file``), the session's buffer has drifted from
that new HEAD. Rather than let it surface as a conflict at save time, we fold
the new HEAD into the live buffer immediately and push the result to
participants — the agent's edit just appears, exactly like a teammate's edit.

Merge cleanliness comes from *this* direction (buffer absorbs the agent's
change), not from checkpoint frequency; once the buffer sits on top of the new
HEAD, the eventual checkpoint has nothing to reconcile.

Overlap handling: a clean 3-way merge is folded in directly. A true line-level
conflict is handed to the checkpoint engine's AI 3-way merge (enqueued now, not
deferred to the periodic scan), which resolves it, commits, and syncs the
merged result back into the buffer for the live participants to see and adjust.

See ``Engineering Projects/Agent Wiki Project/design/Co-Editing.md``.
"""

from __future__ import annotations

import logging
from enum import Enum

from app.wiki import coedit, coedit_channel
from app.wiki import git as wiki_git

log = logging.getLogger(__name__)


class RebaseOutcome(str, Enum):
    """Result of ``rebase_session``."""

    SKIP = "skip"  # session gone, closed, or already based on head_sha
    APPLIED = "applied"  # clean fold; buffer replaced, resync sent
    NOOP = "noop"  # buffer already matched the merge; only base_sha advanced
    CONFLICT = "conflict"  # overlap — caller enqueues a checkpoint to resolve
    RACED = "raced"  # a human op landed mid-merge; skipped (checkpoint is backstop)


def rebase_session(session_id: int, head_sha: str) -> RebaseOutcome:
    """Fold the commit at ``head_sha`` into the session's live buffer.

    Pure domain logic: it does not enqueue anything, so this module stays free of
    the tasks layer (the trigger + the on-conflict checkpoint enqueue live in
    ``app/tasks/coedit_rebase.py``, which acts on a ``CONFLICT`` outcome).
    """
    sess = coedit.get_session(session_id)
    if sess is None or sess.status != "active" or sess.base_sha == head_sha:
        return RebaseOutcome.SKIP
    # A stale task can carry a head_sha the session has already moved past: a
    # concurrent checkpoint, or a later commit's rebase, may have advanced
    # base_sha to a descendant of head_sha. Rebasing "onto" an ancestor would
    # merge the buffer against older content and revert already-committed edits,
    # so skip when head_sha is an ancestor of (already contained in) base_sha.
    if sess.base_sha is not None and wiki_git.is_ancestor(head_sha, sess.base_sha):
        return RebaseOutcome.SKIP

    base_body = wiki_git.read_file_opt(sess.path, ref=sess.base_sha) if sess.base_sha else ""
    current_body = wiki_git.read_file_opt(sess.path, ref=head_sha)
    mr = wiki_git.merge_content(base_body or "", current_body or "", sess.buffer_text)
    if not mr.clean:
        # Overlap: leave the buffer alone; the caller hands it to the checkpoint
        # engine's AI-merge, which resolves + commits + syncs the buffer back.
        log.info("coedit live-rebase: conflict on %s", sess.path)
        return RebaseOutcome.CONFLICT

    res = coedit.rebase_onto(
        session_id,
        base_version=sess.version,
        merged_text=mr.merged,
        new_base_sha=head_sha,
        checkpointed=False,
    )
    if res is None:
        return RebaseOutcome.RACED
    if not res.changed:
        return RebaseOutcome.NOOP
    coedit_channel.broadcast_resync(session_id, res.session.version)
    return RebaseOutcome.APPLIED
