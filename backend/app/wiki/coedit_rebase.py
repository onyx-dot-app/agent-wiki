"""Live-rebase: react to an inbound agent/ingest commit landing under an open
co-edit session.

When a page under a live session is committed to git out of band (an agent,
an ingest job, or a classic ``PUT /file``), the session's live Yjs doc has
drifted from that new HEAD. Unlike the old OT-era buffer (a flat string a
clean 3-way *text* merge could fold in directly), reconciling an external
diff into a *live CRDT doc* block-by-block is real, separate design work —
out of scope here. Instead, every external commit during a live session is
treated the same way the OT path already treated a true conflict: hand off
to the checkpoint engine's AI 3-way merge (``checkpoint_ydoc_session``),
which commits the session's current live content, merges it against the new
HEAD, and syncs the merged result back — participants see a resync rather
than a silent absorb, but nothing is lost or silently stale.

See ``Engineering Projects/Agent Wiki Project/design/Co-Editing.md``.
"""

from __future__ import annotations

import logging
from enum import Enum

from app.wiki import coedit
from app.wiki import git as wiki_git

log = logging.getLogger(__name__)


class RebaseOutcome(str, Enum):
    """Result of ``rebase_session``."""

    SKIP = "skip"  # session gone, closed, or already based on head_sha
    NEEDS_CHECKPOINT = "needs_checkpoint"  # external commit landed — caller enqueues a checkpoint


def rebase_session(session_id: int, head_sha: str) -> RebaseOutcome:
    """Decide whether the commit at ``head_sha`` requires reconciling into
    the session's live doc.

    Pure domain logic: it does not enqueue anything, so this module stays
    free of the tasks layer (the checkpoint enqueue lives in
    ``app/tasks/coedit_rebase.py``, which acts on a ``NEEDS_CHECKPOINT``
    outcome).
    """
    sess = coedit.get_session(session_id)
    if sess is None or sess.status != coedit.SessionStatus.ACTIVE.value or sess.base_sha == head_sha:
        return RebaseOutcome.SKIP
    # A stale task can carry a head_sha the session has already moved past: a
    # concurrent checkpoint, or a later commit's rebase, may have advanced
    # base_sha to a descendant of head_sha. Rebasing "onto" an ancestor would
    # merge the live doc against older content and revert already-committed
    # edits, so skip when head_sha is an ancestor of (already contained in)
    # base_sha.
    if sess.base_sha is not None and wiki_git.is_ancestor(head_sha, sess.base_sha):
        return RebaseOutcome.SKIP
    log.info("coedit live-rebase: external commit on %s, checkpoint will reconcile", sess.path)
    return RebaseOutcome.NEEDS_CHECKPOINT
