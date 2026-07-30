"""Live-rebase — fold an out-of-band commit into an open co-edit session.

An "out-of-band" commit is anything that lands on a page's git history
while a session is open and isn't that session's own checkpoint — an agent
edit, a connector ingest, another human's direct save. Without this, the
session's live doc would silently diverge from git until its own next
checkpoint's 3-way merge (``coedit_checkpoint.py``) reconciles it —
correct, but the divergence is invisible to editors in the meantime and the
merge is deferred to whenever the session happens to go idle.

Re-seed + full resync, not incremental CRDT translation: the room's ``Doc``
is dropped and reconstructed fresh from the 3-way-merged text
as an ordinary logged Yjs update, so connected clients receive it as normal
(``ResyncFrame``) rather than receive the fold-in as an incremental Yjs
update. A true CRDT-native translator would need to turn the merge's text
diff back into structural Yjs ops — the same block-level diffing machinery
``markdown_splice.checkpoint_body`` already does, just run in reverse — for
an event this infrequent (a concurrent external edit landing mid-session).
Simpler and just as correct; costs connected clients one resync round-trip.

Pure domain logic: does not decide when to run or how to reach the right
process (the trigger + the cross-process fan-out live in
``app/tasks/coedit_rebase.py``). Can only ever run in the process that
holds the session's room — a ``pycrdt.Doc`` is thread-affine (see
``app/wiki/coedit_live.py``) — so there is no "not this process's session"
here means: not "no session", just "not this process's session to rebase".
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

    def _bodies() -> tuple[str, str]:
        base = wiki_git.read_file_opt(sess.path, ref=sess.base_sha) if sess.base_sha else ""
        current = wiki_git.read_file_opt(sess.path, ref=head_sha)
        return base or "", current or ""

    base_body, current_body = _bodies()
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

    seq = coedit.apply_update(
        session_id, update_bytes=update_bytes, author_user_id=coedit.SYSTEM_AUTHOR_ID
    )
    if seq is None:
        return RebaseOutcome.SKIP  # session closed underneath us
    coedit_channel.broadcast_yjs(session_id, create_update_message(update_bytes), seq)
    coedit.set_base_sha(session_id, head_sha)
    return RebaseOutcome.APPLIED
