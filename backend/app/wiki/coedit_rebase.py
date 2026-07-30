"""Live-rebase — fold an out-of-band commit into an open co-edit session.

An "out-of-band" commit is anything that lands on a page's git history
while a session is open and isn't that session's own checkpoint — an agent
edit, a connector ingest, another human's direct save. Without this, the
session's live doc would silently diverge from git until its own next
checkpoint's 3-way merge (``coedit_checkpoint.py``) reconciles it —
correct, but the divergence is invisible to editors in the meantime and the
merge is deferred to whenever the session happens to go idle.

The fold is an ordinary logged Yjs update, built by diffing the 3-way-merged
text into a rebuilt ``Doc`` (``coedit_live.rebase_delta``) and broadcasting the
resulting delta. Clients integrate it as normal traffic and rebase their own
pending edits over it, keeping their carets.

Not a re-seed: replacing the document with a fresh one seeded from the merged
text mints a new CRDT lineage, so any update a client had in flight against the
old lineage becomes unintegrable — exactly the divergence this is supposed to
prevent. A delta commutes with concurrent keystrokes instead, which is also why
nothing here needs a compare-and-swap.

Pure domain logic: does not decide when to run (that's
``app/tasks/coedit_rebase.py``). Any process can run it — the document comes
from ``(ydoc_snapshot, coedit_updates)``, not from one worker's memory.
"""

from __future__ import annotations

import logging
from enum import Enum

from pycrdt import create_update_message

from app.wiki import coedit, coedit_live, coedit_channel
from app.wiki import git as wiki_git

log = logging.getLogger(__name__)

class RebaseOutcome(str, Enum):
    """Result of ``rebase_session``."""

    SKIP = "skip"  # session gone/closed, or already based on head_sha
    APPLIED = "applied"  # clean fold, logged and broadcast as an ordinary update
    NOOP = "noop"  # merge collapsed to what the document already had
    CONFLICT = "conflict"  # overlap — caller falls back to the checkpoint engine's AI merge


def rebase_session(session_id: int, head_sha: str) -> RebaseOutcome:
    """Fold the commit at ``head_sha`` into the session's document.

    Plain sync: nothing here is bound to an event loop any more, because
    nothing is bound to a process. Any worker can do this: the document is
    rebuilt from
    ``(ydoc_snapshot, coedit_updates)`` rather than read out of one worker's
    memory, so there is no "not my room, skip" case left.

    The fold is an ordinary logged, broadcast Yjs update. Because updates
    commute, a concurrent keystroke needs no guarding — which is why the
    ``RACED`` outcome, the generation check, the snapshot swap and the
    ``expected_seq`` compare-and-swap are all gone. Clients receive it as
    normal traffic and rebase their own pending edits over it, instead of being
    told to reconnect and losing their caret.
    """
    sess = coedit.get_session(session_id)
    if sess is None or sess.status != coedit.SessionStatus.ACTIVE.value:
        return RebaseOutcome.SKIP
    if sess.base_sha == head_sha:
        return RebaseOutcome.SKIP
    # A stale trigger can carry a head_sha the session has already moved past:
    # a concurrent checkpoint, or a later commit's rebase, may have advanced
    # base_sha to a descendant of head_sha. Merging against that older content
    # would compute a diff that reverts already-committed edits, so skip when
    # head_sha is already contained in base_sha.
    if sess.base_sha is not None and wiki_git.is_ancestor(head_sha, sess.base_sha):
        return RebaseOutcome.SKIP

    base_body = wiki_git.read_file_opt(sess.path, ref=sess.base_sha) if sess.base_sha else ""
    current_body = wiki_git.read_file_opt(sess.path, ref=head_sha)
    base_body, current_body = base_body or "", current_body or ""
    outcome = coedit_live.rebase_delta(session_id, base_body, current_body)
    if outcome is None:
        return RebaseOutcome.SKIP
    update_bytes, _merged, clean = outcome
    if not clean:
        # Overlap: leave the document alone. The caller hands it to the
        # checkpoint engine's AI merge, which resolves and commits.
        log.info("coedit live-rebase: conflict on %s", sess.path)
        return RebaseOutcome.CONFLICT

    if update_bytes is None:
        # Nothing to fold in; only the merge base moves, so the next checkpoint
        # diffs against the right commit.
        coedit.set_base_sha(session_id, head_sha)
        return RebaseOutcome.NOOP

    # author_user_id=None: the server produced this update, not a person.
    seq = coedit.apply_update(session_id, update_bytes=update_bytes, author_user_id=None)
    if seq is None:
        return RebaseOutcome.SKIP  # session closed underneath us
    coedit_channel.broadcast_yjs(session_id, create_update_message(update_bytes), seq)
    coedit.set_base_sha(session_id, head_sha)
    return RebaseOutcome.APPLIED
