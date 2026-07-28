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
(``coedit_room.reseed``), and connected clients are told to reconnect
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
``coedit_room.py``) — which is exactly what ``get_room`` returning ``None``
here means: not "no session", just "not this process's session to rebase".
"""

from __future__ import annotations

import asyncio
import logging
from enum import Enum

from app.models.coedit import ResyncFrame
from app.wiki import coedit, coedit_channel, coedit_room
from app.wiki import git as wiki_git
from app.wiki import markdown_yjs

log = logging.getLogger(__name__)


class RebaseOutcome(str, Enum):
    """Result of ``rebase_session``."""

    SKIP = "skip"  # no room here, session gone/closed, or already based on head_sha
    APPLIED = "applied"  # clean fold; doc re-seeded, resync sent
    NOOP = "noop"  # merge collapsed to what the doc already had; only base_sha advanced
    CONFLICT = "conflict"  # overlap — caller falls back to the checkpoint engine's AI merge
    RACED = "raced"  # session went inactive between the merge and recording it


async def rebase_session(session_id: int, head_sha: str) -> RebaseOutcome:
    """Fold the commit at ``head_sha`` into the session's live doc, if this
    process holds its room."""
    room = coedit_room.get_room(session_id)
    if room is None:
        return RebaseOutcome.SKIP

    def _load_sess() -> coedit.SessionRow | None:
        sess = coedit.get_session(session_id)
        if sess is None or sess.status != coedit.SessionStatus.ACTIVE.value:
            return None
        if sess.base_sha == head_sha:
            return None
        # A stale trigger can carry a head_sha the session has already moved
        # past: a concurrent checkpoint, or a later commit's rebase, may
        # have advanced base_sha to a descendant of head_sha. Rebasing
        # "onto" an ancestor would merge the doc against older content and
        # revert already-committed edits, so skip when head_sha is already
        # contained in base_sha — this also covers the session's own
        # checkpoint commit landing as the after_doc_write callback that
        # triggered this rebase in the first place.
        if sess.base_sha is not None and wiki_git.is_ancestor(head_sha, sess.base_sha):
            return None
        return sess

    sess = await asyncio.to_thread(_load_sess)
    if sess is None:
        return RebaseOutcome.SKIP

    # room_body and expected_generation, together — a Doc read plus
    # Room.generation's own snapshot, both inline on this task's own
    # thread (the event loop), no `await` between them, so both reflect
    # the exact same instant of room.doc. This, not sess.ydoc_seq (already
    # stale by this point — read inside _load_sess's own to_thread call,
    # strictly *before* this line runs, so a concurrent edit landing in
    # that gap is reflected in room_body but not in sess.ydoc_seq; caught
    # in review), is the real baseline: see Room.generation's own
    # docstring for why ydoc_seq can't serve this role at all — a local
    # edit mutates room.doc synchronously but its DB log write is a
    # separate, awaited step, so ydoc_seq can lag room.doc by a real
    # window regardless of when it's read.
    room_body = markdown_yjs.reconstruct_body(room.doc)
    expected_generation = room.generation

    def _merge() -> wiki_git.MergeResult:
        base_body = wiki_git.read_file_opt(sess.path, ref=sess.base_sha) if sess.base_sha else ""
        current_body = wiki_git.read_file_opt(sess.path, ref=head_sha)
        return wiki_git.merge_content(base_body or "", current_body or "", room_body)

    mr = await asyncio.to_thread(_merge)
    if not mr.clean:
        # Overlap: leave the doc alone; the caller hands it to the
        # checkpoint engine's AI-merge, which resolves + commits + re-seeds
        # the room from the result.
        log.info("coedit live-rebase: conflict on %s", sess.path)
        return RebaseOutcome.CONFLICT

    def _snapshot_for(merged: str) -> bytes:
        # A throwaway Doc, seeded and immediately discarded after reading its
        # bytes — never touched again, so building it off-loop is safe (same
        # as coedit_checkpoint.py's own snapshot-on-diverge case).
        return markdown_yjs.seed_doc_from_markdown(merged).get_update()

    snapshot = await asyncio.to_thread(_snapshot_for, mr.merged)
    res = await asyncio.to_thread(
        coedit.rebase_onto,
        session_id,
        new_base_sha=head_sha,
        snapshot=snapshot,
        body=mr.merged,
        # A secondary, DB-level guard (best-effort — see room.generation's
        # check below for the one that actually matters for room.doc's own
        # safety): catches a concurrent edit whose DB log write landed
        # during the awaits above, pruning coedit_updates and advancing
        # ydoc_snapshot_seq only when nothing new has been logged since
        # _load_sess observed this seq.
        expected_seq=sess.ydoc_seq,
        checkpointed=False,
    )
    if res is None:
        return RebaseOutcome.RACED

    # The real gate, checked immediately before reseed with no `await` in
    # between — a concurrent edit landing anywhere above (whether or not
    # its DB log write has completed yet) bumps this, and reseeding over
    # it would replace a Doc that edit already touched with a fresh one
    # its own update can never integrate into (confirmed in review — see
    # Room.generation's own docstring). rebase_onto's own DB write above
    # may already have advanced base_sha/the persisted snapshot at this
    # point regardless of this check failing; that's fine — it's
    # consistent with mr.merged, just not safe to apply to *this* room's
    # Doc anymore, and a future checkpoint reconciles the room normally
    # from the DB state either way (see app/wiki/coedit_checkpoint.py).
    if room.generation != expected_generation:
        return RebaseOutcome.RACED
    if mr.merged == room_body:
        return RebaseOutcome.NOOP

    # Reseed from this same snapshot, not an independent
    # seed_doc_from_markdown(mr.merged) call inside reseed() — two separate
    # seedings of "the same" text produce incompatible CRDT lineages (see
    # coedit_room.reseed), which would silently break a later checkpoint's
    # replay against the ydoc_snapshot just persisted above (caught in
    # review).
    coedit_room.reseed(room, snapshot, mr.merged, head_sha)
    coedit_channel.publish_control(session_id, ResyncFrame(session_id=session_id).model_dump())
    return RebaseOutcome.APPLIED
