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

from pycrdt import Doc

from app.wiki import coedit, coedit_live, coedit_channel, wiki_documents
from app.wiki import git as wiki_git
from app.wiki import markdown_splice

log = logging.getLogger(__name__)


class RebaseOutcome(str, Enum):
    """Result of ``rebase_session``."""

    SKIP = "skip"  # session gone/closed, or already based on head_sha
    APPLIED = "applied"  # clean fold, logged and broadcast as an ordinary update
    NOOP = "noop"  # merge collapsed to what the document already had
    CONFLICT = "conflict"  # overlap — caller falls back to the checkpoint engine's AI merge


def rebase_session(session_id: int, head_sha: str) -> RebaseOutcome:
    """Fold the commit at ``head_sha`` into the session's document.

    Plain sync, and callable from any process: the document comes from
    ``(ydoc_snapshot, coedit_updates)``, not from one worker's memory.

    The fold is an ordinary logged, broadcast Yjs update. Because updates
    commute, a concurrent keystroke needs no guarding — hence no
    compare-and-swap, no snapshot swap, and no "raced, try again" outcome.
    Clients receive it as normal traffic and rebase their own pending edits
    over it, keeping their carets.
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
    # expected_lineage: the delta was built from the document as of ``sess``;
    # if a checkpoint reseeded the session in the meantime, folding it in
    # would poison the new lineage — refuse and let the trigger re-run
    # against the settled state.
    try:
        seq = coedit.apply_update(
            session_id,
            update_bytes=update_bytes,
            author_user_id=None,
            expected_lineage=sess.ydoc_lineage,
        )
    except coedit.StaleLineageError:
        return RebaseOutcome.SKIP
    if seq is None:
        return RebaseOutcome.SKIP  # session closed underneath us
    coedit_channel.broadcast_yjs(session_id, create_update_message(update_bytes), seq)
    coedit.set_base_sha(session_id, head_sha)
    return RebaseOutcome.APPLIED


def rebase_document_row(path: str, head_sha: str) -> RebaseOutcome:
    """Fold an out-of-band commit into the page's ``wiki_documents`` row when
    no session is open.

    The row otherwise advances only on checkpoints, so a stretch of git-only
    writes (agent edits, ingestion) leaves it holding old content — and every
    later open transplants that old content into the new session, riding the
    conflict path to reconcile drift that could have been folded here flat.

    With no session there are no concurrent edits: the row's own body is the
    merge base, so the fold is a pure splice of HEAD onto the row's lineage —
    no three-way, no conflict outcome. ``SKIP`` when there is nothing to do
    (no row, already current, stale trigger) or when the splice finds no safe
    block pairing — the open-time fold handles that page as before.
    """
    sess = coedit.get_active_session(path)
    if sess is not None:
        return RebaseOutcome.SKIP  # the session fold path owns the page
    row = wiki_documents.get(path)
    if row is None:
        return RebaseOutcome.SKIP
    row_base = row["base_sha"]
    assert row_base is None or isinstance(row_base, str)
    if row_base == head_sha:
        return RebaseOutcome.SKIP
    if row_base is not None and wiki_git.is_ancestor(head_sha, row_base):
        return RebaseOutcome.SKIP  # stale trigger — the row moved past it
    head_body = wiki_git.read_file_opt(path, ref=head_sha) or ""
    row_snapshot, row_body = row["ydoc_snapshot"], row["ydoc_snapshot_body"]
    assert isinstance(row_snapshot, bytes) and isinstance(row_body, str)

    doc = Doc()
    doc.apply_update(row_snapshot)
    if not markdown_splice.apply_markdown_diff(doc, row_body, head_body):
        # No safe block pairing (the same positional-drift guard the
        # checkpoint splice has). Reseeding would mint a fresh lineage a
        # disconnected client may still hold the old one of — leave the row
        # and let the open-time fold reconcile, as before this function.
        log.info("coedit row-rebase: no safe splice for %s; leaving row", path)
        return RebaseOutcome.SKIP
    markdown_splice.restamp_block_ids(doc, head_body)
    if not wiki_documents.advance_offline(
        path,
        snapshot=doc.get_update(),
        body=head_body,
        base_sha=head_sha,
        expected_base_sha=row_base,
    ):
        return RebaseOutcome.SKIP  # a session opened or a checkpoint landed first
    return RebaseOutcome.APPLIED
