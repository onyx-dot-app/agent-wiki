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

from app.wiki import coedit, coedit_channel
from app.wiki import git as wiki_git

log = logging.getLogger(__name__)


def rebase_session(session_id: int, head_sha: str, actor: str | None) -> str:
    """Fold the commit at ``head_sha`` into the session's live buffer.

    Returns a short status string (for logging/tests): ``"skip"`` (gone, closed,
    or already based on ``head_sha``), ``"applied"`` (clean fold broadcast),
    ``"noop"`` (buffer already matched; only ``base_sha`` advanced),
    ``"conflict"`` (overlap → checkpoint enqueued), or ``"raced"`` (a human op
    landed mid-merge; skipped — the checkpoint merge is the backstop).
    """
    sess = coedit.get_session(session_id)
    if sess is None or sess.status != "active" or sess.base_sha == head_sha:
        return "skip"

    base_body = wiki_git.read_file_opt(sess.path, ref=sess.base_sha) if sess.base_sha else ""
    current_body = wiki_git.read_file_opt(sess.path, ref=head_sha)
    mr = wiki_git.merge_content(base_body or "", current_body or "", sess.buffer_text)
    if not mr.clean:
        # Overlap: the checkpoint engine's AI-merge resolves it, commits, and
        # syncs the merged buffer back to participants. Enqueue now.
        from app.tasks.coedit_checkpoint import checkpoint_coedit_session

        checkpoint_coedit_session(session_id)
        log.info("coedit live-rebase: conflict on %s → checkpoint enqueued", sess.path)
        return "conflict"

    res = coedit.reconcile_onto(
        session_id,
        base_version=sess.version,
        old_buffer=sess.buffer_text,
        merged_text=mr.merged,
        new_base_sha=head_sha,
        checkpointed=False,
    )
    if res is None:
        return "raced"
    row, change = res
    if change is None:
        return "noop"
    coedit_channel.broadcast_op(session_id, row.version, [change], author_user_id=actor or "agent")
    return "applied"
