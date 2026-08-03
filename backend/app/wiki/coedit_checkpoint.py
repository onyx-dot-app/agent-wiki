"""Checkpoint a co-edit session's live Yjs doc back into git.

Rebuilds a throwaway ``pycrdt.Doc`` from the session's durable state —
``ydoc_snapshot`` (a binary snapshot at ``ydoc_snapshot_seq``) plus every
``coedit_updates`` row logged since — and commits through the *existing*
write gateway, reconciling any agent/ingest commit that landed meanwhile via
the same 3-way + AI merge. Durability is the update log + snapshot in
Postgres, so a checkpoint is about visibility (making the committed page
fresh for readers/search/agents) and bounding merge size — not data safety.

The ``Doc`` is built here and dropped here. That's what lets this run as a
plain ``coedit_queue`` task (``app/tasks/coedit_checkpoint.py``) dispatched to
any worker: a ``Doc`` is thread-affine (PyO3-unsendable — see
``coedit_live.py``), so one shared across threads couldn't be touched from a
worker's thread at all.

When the merge changes content beyond what the editors have on screen, they
are sent the difference as an ordinary Yjs update — see the broadcast at the
end of ``checkpoint_session``.

Attribution: the commit author is the last editor (so git blame credits
whoever last touched the doc); the other session participants are added as
``Co-authored-by:`` trailers. See
``Engineering Projects/Agent Wiki Project/design/Co-Editing.md``.
"""

from __future__ import annotations

import logging

from pycrdt import Doc, XmlElement, XmlFragment, create_update_message
from pydantic import BaseModel, ConfigDict

from app.auth import User, set_current_user
from app.auth import users as users_repo
from app.models.wiki import ChangeKind
from app.wiki import coedit, coedit_channel, filesystem
from app.wiki import drafts as wiki_drafts
from app.wiki import git as wiki_git
from app.wiki import markdown_blocks, markdown_splice, markdown_yjs
from app.wiki.markdown_splice import TouchedTracker

log = logging.getLogger(__name__)

# Bound on folding a late-arriving row into the *same* checkpoint attempt
# (see checkpoint_session's own docstring) before giving up and finalizing
# on the last attempt regardless. Each attempt is a real git commit —
# additive history, never amended, but still real commits — so this stays
# small; the realistic case (one or two stray keystrokes landing during a
# single git-commit-plus-AI-merge window) converges in one retry, and a
# session that's still dirty after the bound is hit just gets picked up
# completely fresh by the next trigger.
_LATE_UPDATE_FOLD_ATTEMPTS = 3


def _duplicated_block_ids(doc: Doc) -> list[str]:
    """Top-level ``_blockId``s appearing more than once, in document order.

    Two top-level blocks sharing an id is not a thing editing can produce — it
    means the document holds the same block twice under two CRDT lineages that
    share no item ids and therefore cannot dedupe (``open_session``'s reuse rule
    exists to stop that arising; this catches any other route to it). Freshly
    typed content carries no id at all and takes ``checkpoint_body``'s
    ``orig is None`` path, so it can't collide.

    Committing in this state duplicates content rather than merely mis-ordering
    it: ``markdown_splice.checkpoint_body`` keys a live block back to its range
    in the committed markdown by id, so both copies resolve to the same range —
    the same reason the editor enforces uniqueness client-side (see
    ``UniqueBlockIdentity`` in ``frontend/src/lib/editor/blocks.ts``).
    """
    seen: set[str] = set()
    dupes: list[str] = []
    root = doc.get(markdown_yjs.ROOT_XML_KEY, type=XmlFragment)
    for child in root.children:
        block_id = dict(getattr(child, "attributes", {})).get(markdown_yjs.BLOCK_ID_ATTR)
        if not isinstance(block_id, str):
            continue
        if block_id in seen and block_id not in dupes:
            dupes.append(block_id)
        seen.add(block_id)
    return dupes


def drop_duplicate_blocks(doc: Doc) -> list[str]:
    """Repair repeats of a top-level ``_blockId``, keeping the first of each,
    and return the ids that were repeated.

    Two different situations produce a repeated id, and they need opposite
    repairs:

    A **content-identical** repeat is a lineage merge — the same block
    integrated twice. It is *deleted*, deliberately, rather than declined:
    the duplicated content is in the connected browsers' own documents too,
    so refusing here leaves them holding it and re-sending it on the next
    reconnect — closing the session doesn't help either, since the next one
    can't reuse a dirty session and so seeds yet another lineage for the
    same retained document to merge into. Deleting escapes that: the
    deletion is broadcast at the end of ``checkpoint_session`` like any
    other update, and because a Yjs delete addresses the same items the
    client holds, the client's document converges to the repaired state
    without a reload (verified directly against pycrdt).

    A repeat with **different content** is an ordinary edit caught
    mid-flight: ProseMirror's node split copies attrs — the id included —
    onto both halves, and the editor's ``UniqueBlockIdentity`` clears the
    second one in a separate follow-up update. A checkpoint landing between
    the two sees the duplicate the client is about to fix. Deleting here
    destroys what the person just typed (observed live: a paragraph lifted
    out of a list, still carrying the list's id, eaten by the checkpoint
    that raced the fix-up). Instead the later child's id is *cleared* —
    exactly what the client's own repair would do — which routes it through
    ``checkpoint_body``'s ``orig is None`` path as the new content it is.
    """
    dupes = _duplicated_block_ids(doc)
    if not dupes:
        return []
    root = doc.get(markdown_yjs.ROOT_XML_KEY, type=XmlFragment)
    first_text: dict[str, str] = {}
    stale: list[int] = []
    reidentify: list[int] = []
    for i, child in enumerate(root.children):
        block_id = dict(getattr(child, "attributes", {})).get(markdown_yjs.BLOCK_ID_ATTR)
        if not isinstance(block_id, str):
            continue
        if block_id not in first_text:
            first_text[block_id] = markdown_yjs.serialize_block(child)
            continue
        if markdown_yjs.serialize_block(child) == first_text[block_id]:
            stale.append(i)
        else:
            reidentify.append(i)
    # Descending, so each deletion can't shift the index of one still pending.
    with doc.transaction():
        for i in reidentify:
            root.children[i].attributes[markdown_yjs.BLOCK_ID_ATTR] = None
        for i in reversed(stale):
            del root.children[i]
    return dupes


# A lineage collision restates most of the page; a person can legitimately
# retype one line that already exists elsewhere on it. Both floors must clear
# before anything is deleted, so the small, ordinary case is never touched.
_RESTATED_MIN_BLOCKS = 5
_RESTATED_MIN_FRACTION = 0.25


class _Restatement(BaseModel):
    """Where a doc restates the page back onto itself, as child indices.

    ``restated`` is the duplicated content itself, and the only thing the
    floors are measured against. ``fillers`` are the blank-line blocks the
    duplicated copy brought with it — the codec makes every newline its own
    top-level block, so a restated copy arrives interleaved with them.
    Deleting content without them leaves a run of blank lines behind where the
    copy was.
    """

    model_config = ConfigDict(frozen=True)

    restated: list[int]
    fillers: list[int]

    def drop_indices(self) -> list[int]:
        return sorted(self.restated + self.fillers)


def _restated_base_blocks(doc: Doc, base_body: str) -> _Restatement:
    """Child indices holding a second copy of a base block already being
    emitted by id, in document order.

    Catches the lineage collision that ``_duplicated_block_ids`` cannot see.
    That one keys on an id repeating *within* the doc, but a foreign lineage's
    children need not repeat a base id to be duplicated into the page — any id
    ``base_body`` doesn't know takes ``checkpoint_body``'s ``orig is None``
    path and is re-serialized as though freshly typed, appended alongside the
    base copy that is emitted verbatim. That is the 87-insertions/0-deletions
    shape: nothing conflicts, so nothing dedupes.

    The test is content, not ids, because the ids in this state can't be
    trusted to any particular shape. Two conditions keep it off legitimate
    edits:

    the child carries an id at all
        Freshly typed content carries none (see ``_duplicated_block_ids``),
        while a foreign lineage's blocks come from a seeded or restamped doc
        and always have one. So an id-less child is the user's own new
        content and is never restatement — however much of the page it is,
        and however exactly it repeats something already there. Five new
        ``---`` rules or five repeated headings are ordinary editing.
    the id isn't one ``base_body`` carries
        Those are emitted verbatim from ``base_body`` and are the copy being
        kept, not a duplicate of it.
    the base block it restates is still carried by some child
        So its content is definitely being emitted. Without this, a block the
        user deleted and retyped identically would be read as a duplicate of a
        copy that is no longer there, and dropping it would lose the text.
    """
    base_ranges = markdown_blocks.top_level_block_ranges(base_body)
    base_ids = {b.block_id for b in base_ranges}
    root = doc.get(markdown_yjs.ROOT_XML_KEY, type=XmlFragment)
    children = list(root.children)

    carried_ids: set[str] = set()
    for child in children:
        block_id = dict(getattr(child, "attributes", {})).get(markdown_yjs.BLOCK_ID_ATTR)
        if isinstance(block_id, str):
            carried_ids.add(block_id)

    emitted_text: set[str] = set()
    for b in base_ranges:
        if b.block_id not in carried_ids:
            continue
        text = base_body[b.start : b.end].strip()
        if text:
            emitted_text.add(text)

    restated: list[int] = []
    foreign_blanks: list[int] = []
    for i, child in enumerate(children):
        if not isinstance(child, XmlElement):
            continue
        block_id = dict(child.attributes).get(markdown_yjs.BLOCK_ID_ATTR)
        if not isinstance(block_id, str) or block_id in base_ids:
            continue
        text = markdown_yjs.serialize_block(child).strip()
        if not text:
            foreign_blanks.append(i)
        elif text in emitted_text:
            restated.append(i)

    # Only the blanks sitting within the restated run, so a blank line the
    # user left elsewhere on the page is untouched.
    if not restated:
        return _Restatement(restated=[], fillers=[])
    first, last = restated[0], restated[-1]
    fillers = [i for i in foreign_blanks if first < i < last]
    return _Restatement(restated=restated, fillers=fillers)


def drop_restated_blocks(doc: Doc, base_body: str) -> int:
    """Delete children that restate the page back onto itself; return how many.

    Deleted rather than refused, for the same reason as
    ``drop_duplicate_blocks``: the duplication is in the connected browsers'
    documents too, and a Yjs delete addresses the items they hold, so the
    checkpoint's broadcast converges them. Refusing would leave every client
    holding it, to be re-sent on the next reconnect.

    Both floors in ``_RESTATED_MIN_BLOCKS``/``_RESTATED_MIN_FRACTION`` must
    clear. One or two restating blocks is something a person does; half the
    page is not reachable by editing.
    """
    found = _restated_base_blocks(doc, base_body)
    root = doc.get(markdown_yjs.ROOT_XML_KEY, type=XmlFragment)
    total = len(root.children)
    if len(found.restated) < _RESTATED_MIN_BLOCKS:
        return 0
    if len(found.restated) < total * _RESTATED_MIN_FRACTION:
        return 0
    # Descending, so each deletion can't shift the index of one still pending.
    with doc.transaction():
        for i in reversed(found.drop_indices()):
            del root.children[i]
    return len(found.restated)


def _user(user_id: str) -> User | None:
    row = users_repo.get_by_id(user_id)
    if row is None:
        return None
    return User(id=row["id"], email=row["email"], name=row["name"], is_admin=bool(row["is_admin"]))


def _commit_message(session_id: int, *, primary_author_id: str | None) -> str:
    """`Co-authored-by:` trailers for every participant except the primary
    author (git credits the primary author separately via the commit author)."""
    lines = ["Co-editing checkpoint"]
    trailers: list[str] = []
    for p in coedit.list_participants(session_id):
        if p.user_id == primary_author_id:
            continue
        u = users_repo.get_by_id(p.user_id)
        if u is not None:
            trailers.append(f"Co-authored-by: {u['name'] or u['email']} <{u['email']}>")
    if trailers:
        lines.append("")
        lines.extend(trailers)
    return "\n".join(lines)


def _rebuild_doc(sess: coedit.CheckpointSessionRow) -> tuple[Doc, str, TouchedTracker, int]:
    """Rebuild a throwaway ``Doc`` from ``sess.ydoc_snapshot`` plus every
    update logged since.

    ``base_body`` — the pre-replay text ``markdown_splice.checkpoint_body``
    diffs against — comes from ``sess.ydoc_snapshot_body``, kept in lockstep
    with ``ydoc_snapshot`` by every writer (``set_initial_snapshot`` and
    ``advance_checkpoint`` — see ``app/wiki/coedit.py``), *not* re-derived from
    a git read at ``base_sha``: a live-rebase fold has no corresponding git
    commit of its own, so ``base_sha`` can't always resolve to the content the
    snapshot actually represents the way a real commit ref can (a git-read
    approach silently corrupted the diff base after a mid-session rebase).

    The ``TouchedTracker`` is created right after the doc is seeded, before
    replay, so every replayed update is observed by the tracker exactly as
    it would be for a live edit (``TouchedTracker.observe_deep`` sees any
    mutation regardless of origin) — matching ``coedit_live``'s own rebuild
    own seed-then-track ordering.

    Returns the seq actually replayed up to (the caller's own ``sess.ydoc_seq``
    can be a moment stale by the time this runs — a fresh update can land
    between that read and this one — so this is read fresh here via
    ``updates_since``, and is what the caller must advance the checkpoint
    watermark to, not ``sess.ydoc_seq``: understating it would leave
    ``ydoc_snapshot_seq`` behind what the snapshot bytes actually capture).
    """
    doc = Doc()
    assert sess.ydoc_snapshot is not None  # caller guarantees this (see checkpoint_session)
    doc.apply_update(sess.ydoc_snapshot)
    base_body = sess.ydoc_snapshot_body
    tracker = TouchedTracker(doc)
    since = coedit.updates_since(sess.id, sess.ydoc_snapshot_seq)
    for u in since.updates:
        try:
            doc.apply_update(u.update_payload)
        except Exception:
            # An undecodable payload (a corrupt row, or bytes from an
            # incompatible lineage that somehow reached the log) must not
            # propagate: raising here would retry, exhaust retries, drop the
            # task, and leave the session ACTIVE and dirty indefinitely while
            # editing carried on producing updates nobody could ever
            # checkpoint. One bad row can't strand the rest of the session's
            # history, which is worth far more than that row's content.
            log.exception(
                "coedit checkpoint: session %s seq %d update failed to apply; skipping",
                sess.id,
                u.seq,
            )
    replayed_seq = since.head_seq if since.head_seq is not None else sess.ydoc_snapshot_seq
    return doc, base_body, tracker, replayed_seq


class CheckpointOutcome(BaseModel):
    """What a successful checkpoint produced, for the caller
    (``app/tasks/coedit_checkpoint.py``) to act on.

    ``sha`` is the checkpoint's own commit — also the session's new
    ``base_sha`` from here on, one value serving both purposes. ``diverged``
    is True when the committed result differs from what the session's document
    held, i.e. the merge folded in content from an out-of-band commit; the
    editors have already been sent that difference as a Yjs update by the time
    this is returned. Carries no snapshot bytes: they are durably in
    ``ydoc_snapshot`` for anyone who needs them, and a whole page's worth
    wouldn't fit the realtime bus's 8000-byte payload cap anyway (see
    ``app/realtime/bus.py``)."""

    model_config = ConfigDict(frozen=True)

    session_id: int
    sha: str
    body: str
    diverged: bool


def checkpoint_session(session_id: int) -> CheckpointOutcome | None:
    """Commit a dirty session's doc to git; return the outcome (or None).

    Pure sync — no asyncio, no Doc access outside the throwaway one this
    function builds and discards. No-op when the session is gone, clean
    (``ydoc_seq == ydoc_checkpointed_seq``), has no snapshot yet (a session
    whose very first connection hasn't finished seeding one — momentary, see
    ``coedit.set_initial_snapshot``), or the merge collapses to the current
    HEAD.
    """
    with coedit.checkpoint_lock(session_id) as acquired:
        if not acquired:
            log.info("coedit checkpoint: session %s busy; skipping (retries)", session_id)
            return None
        sess = coedit.get_session_for_checkpoint(session_id)
        if sess is None:
            return None  # gone
        if sess.status != coedit.SessionStatus.ACTIVE.value:
            # A closed session is finalized — never re-commit it. This dedupes
            # duplicate triggers: once the first checkpoint commits and closes
            # the session, every other pending trigger no-ops here. A closed
            # session should already be clean (close follows a clean
            # checkpoint); if it's somehow dirty, log and skip rather than
            # clobber HEAD with a stale doc — the edits stay in the update log
            # for manual recovery.
            if sess.ydoc_seq != sess.ydoc_checkpointed_seq:
                log.warning(
                    "coedit checkpoint: session %s is %s but dirty (seq %d != "
                    "checkpointed seq %d); skipping — needs manual reconciliation",
                    session_id,
                    sess.status,
                    sess.ydoc_seq,
                    sess.ydoc_checkpointed_seq,
                )
            return None
        if sess.ydoc_seq == sess.ydoc_checkpointed_seq:
            return None  # nothing new to commit
        if sess.ydoc_snapshot is None:
            # This session's very first connection hasn't finished
            # set_initial_snapshot yet — momentary, the next trigger retries.
            log.info("coedit checkpoint: session %s has no snapshot yet; skipping", session_id)
            return None

        path = sess.path
        # The page existed when the session was seeded (base_sha set) but the
        # working-tree file is gone — moved or deleted underneath the session.
        # A move should have re-keyed the session (``coedit.on_path_moved``);
        # committing here would resurrect the dead path from the doc.
        # Working-tree ``is_file`` (not a git read) so a transient git failure
        # can't masquerade as a missing page; an OSError propagates and the
        # trigger retries. Participant-less sessions close (the zombie case;
        # the update log stays for recovery). Sessions with live participants
        # just skip: if a move re-key is landing concurrently, the scan
        # retries after it points the session at the new path.
        if sess.base_sha is not None and not filesystem.absolute(path).is_file():
            if coedit.list_participants(session_id):
                log.warning(
                    "coedit checkpoint: session %s targets missing path %r but "
                    "has participants; skipping (scan retries after any move re-key)",
                    session_id,
                    path,
                )
                return None
            log.warning(
                "coedit checkpoint: session %s targets missing path %r (moved or "
                "deleted); closing — doc left uncommitted",
                session_id,
                path,
            )
            coedit.close_session(session_id)
            return None

        doc, base_body, tracker, replayed_seq = _rebuild_doc(sess)

        # Repaired in place and then committed normally, rather than refused:
        # the checkpoint's closing broadcast carries the deletion to every
        # connected client, so their documents converge too. Refusing instead
        # would leave the duplication in the browsers that hold it, to be
        # re-sent on their next reconnect — and closing the session wouldn't
        # help, since the next one can't reuse a dirty session and would seed
        # yet another lineage for that same retained document to merge into.
        dupes = drop_duplicate_blocks(doc)
        if dupes:
            log.error(
                "coedit checkpoint: %s (session %s) held duplicate top-level block ids "
                "%s; dropped the repeats and committing the repaired document. This "
                "should be unreachable — see open_session's lineage reuse rule.",
                path,
                session_id,
                dupes,
            )

        restated = drop_restated_blocks(doc, base_body)
        if restated:
            log.error(
                "coedit checkpoint: %s (session %s) held %d block(s) restating the "
                "page back onto itself; dropped them and committing the repaired "
                "document. Indicates a second Yjs lineage reached the doc — see "
                "open_session's reuse rule and purge_viewer_sessions' retention.",
                path,
                session_id,
                restated,
            )

        change_kind = ChangeKind.EDIT if sess.base_sha else ChangeKind.CREATE

        primary_id = coedit.last_update_author(session_id)
        author = _user(primary_id) if primary_id else None

        # Local import: app.wiki.utils (indirectly, via notify -> tasks) imports
        # back into this module through app.tasks.coedit_checkpoint, so a
        # module-level import here is circular.
        from app.wiki.utils import commit_and_fan_out  # noqa: PLC0415

        # A row logged *after* replayed_seq but *before* this commit lands
        # (the commit-plus-AI-merge call below takes real wall-clock time)
        # is durably in coedit_updates but isn't reflected in what's about
        # to be committed. Persisting a snapshot/watermark past it anyway
        # would silently strand it forever: the moment either this merge
        # diverges (the apply_markdown_diff branch below) or a future
        # checkpoint reseeds, the block(s) it touches get a fresh CRDT
        # identity, and a stranded update can never integrate against a
        # lineage it wasn't generated from (confirmed in review, across two
        # earlier attempts at fixing this from the *reconcile* side —
        # after this function has already finalized past a row, there's no
        # undoing it: the row's already deleted and the watermark's already
        # advanced).
        #
        # So: loop. Commit, then check for exactly such a row before ever
        # calling advance_checkpoint. If one landed, it was generated
        # against this doc's own lineage — still fully intact, since
        # nothing has been spliced or reseeded yet — so fold it in and
        # commit again, treating the commit just made as the next
        # attempt's own diff base. Each iteration's commit is real,
        # additive git history (never amended). Bounded so continuous
        # typing can't loop forever; if the bound is hit while a row is
        # still pending, this stops folding and finalizes on the last
        # attempt instead — that row stays unpruned (advance_checkpoint
        # only prunes up to the seq actually captured below), so it isn't
        # lost, just deferred to whatever checkpoint attempt runs next.
        # This narrows the window to "N consecutive commit-plus-merge
        # cycles' worth of edits landing on the same block, back to back,
        # bound never caught up" — not a mathematical guarantee for a
        # pathological non-stop-typing case, but that's a materially
        # smaller window than the single-shot version this replaces.
        result = None
        body = base_body
        for attempt in range(_LATE_UPDATE_FOLD_ATTEMPTS):
            body = markdown_splice.checkpoint_body(base_body, doc, tracker)
            message = _commit_message(session_id, primary_author_id=primary_id)

            # System-initiated write: editors' write permission was already
            # enforced when they joined/applied updates, so skip the ACL gate;
            # not "agent activity".
            with set_current_user(author):
                result = commit_and_fan_out(
                    path,
                    body,
                    message,
                    change_kind=change_kind,
                    base_body=base_body if change_kind == ChangeKind.EDIT else None,
                    ai_merge=True,
                    skip_acl=True,
                    record_activity=False,
                    # This is the session's own commit — don't fold it back
                    # into the session as an inbound rebase.
                    trigger_coedit_rebase=False,
                )

            late = coedit.updates_since(session_id, replayed_seq)
            if not late.updates or attempt == _LATE_UPDATE_FOLD_ATTEMPTS - 1:
                break
            # Fold the late rows into *this* doc — still the same lineage
            # they were generated against — before the next attempt's own
            # checkpoint_body/commit. base_body becomes what was actually
            # just committed (or, for a no-op attempt, what was already at
            # HEAD) so the next attempt's diff is against real git state.
            base_body = result.new_body if result is not None else body
            change_kind = ChangeKind.EDIT
            tracker.reset()
            for u in late.updates:
                try:
                    doc.apply_update(u.update_payload)
                except Exception:
                    log.exception(
                        "coedit checkpoint: session %s seq %d update failed to apply"
                        " while folding a late row; skipping",
                        session_id,
                        u.seq,
                    )
            replayed_seq = late.head_seq if late.head_seq is not None else replayed_seq

        if result is None:
            # The merge produced exactly the current HEAD (doc already
            # matches committed content). Still advance the snapshot/watermark
            # against current HEAD so we don't re-attempt this seq forever —
            # the rebuilt doc's own state (post-replay) is the new snapshot,
            # even though nothing was actually committed.
            head = wiki_git.head_sha_for_path(path)
            if head is None:
                # Shouldn't happen — a no-op merge implies HEAD exists. Surface
                # it rather than silently leaving the session dirty forever.
                log.warning(
                    "coedit checkpoint: no-op merge but no HEAD for %s (session %s); left dirty",
                    path,
                    session_id,
                )
                return None
            # Restamp before snapshotting: this doc is kept as-is (not
            # reseeded from markdown text), so its block/row ids would
            # otherwise drift out of sync with base_body's own numbering
            # the moment a future checkpoint re-parses a base_body whose
            # block count/order has since changed — see
            # restamp_block_ids's own docstring.
            markdown_splice.restamp_block_ids(doc, body)
            coedit.advance_checkpoint(
                session_id, seq=replayed_seq, snapshot=doc.get_update(), body=body, base_sha=head
            )
            return None

        # If the AI/3-way merge changed the content beyond what this doc
        # held (a concurrent external commit folded in), the *committed*
        # result must become the new snapshot's content. Splice it onto
        # this doc's own lineage (apply_markdown_diff) rather than
        # discarding that lineage for a fresh seed_doc_from_markdown parse
        # — any edit logged *up to* replayed_seq is already folded into
        # this doc by the loop above; only a concurrent edit logged beyond
        # the fold-in bound (still live, still landing in the log the whole
        # time — checkpointing never blocks editing) is generated
        # against this doc's lineage without being reflected in it yet,
        # and a fresh, unrelated lineage couldn't integrate it later
        # either way (confirmed in review — silent, total loss, not an
        # error) — preserving lineage here is strictly better regardless.
        # See apply_markdown_diff's own docstring.
        diverged = result.new_body != body
        # The state connected clients are on, captured before the finalizing
        # mutations below, so a divergence can be handed to them as a plain
        # delta (see the broadcast after advance_checkpoint).
        pre_finalize = doc.get_state()
        reseeded = False
        if diverged:
            if not markdown_splice.apply_markdown_diff(doc, body, result.new_body):
                # doc's children don't correspond 1:1 to a fresh parse of
                # their own body — the same positional drift
                # restamp_block_ids guards against, just discovered here
                # first. No safe way to splice without risking a wrong
                # pairing; fall back to the old, lineage-discarding
                # behavior rather than risk misapplying the diff (no worse
                # than before this fix for this rarer, compounding case).
                doc = markdown_yjs.seed_doc_from_markdown(result.new_body)
                reseeded = True
            else:
                markdown_splice.restamp_block_ids(doc, result.new_body)
        else:
            markdown_splice.restamp_block_ids(doc, body)
        snapshot = doc.get_update()

        coedit.advance_checkpoint(
            session_id,
            seq=replayed_seq,
            snapshot=snapshot,
            body=result.new_body,
            base_sha=result.sha,
        )
        wiki_drafts.clear_if_diverged(path, result.new_body)
        if diverged:
            if reseeded:
                # A fresh lineage shares no history with what clients hold, so
                # there is no delta to send: they converge on their next
                # reconnect handshake, against the snapshot just written.
                log.warning(
                    "coedit checkpoint: session %s reseeded on divergence; connected "
                    "clients stay on pre-merge content until they reconnect",
                    session_id,
                )
            else:
                # Durable state first (above), then tell the editors. Sent as an
                # ordinary update — the same lineage they're on, so they
                # integrate it and rebase their own pending edits over it. seq
                # is None because this isn't a logged row: the snapshot carries
                # it durably, and logging it would leave ydoc_seq ahead of the
                # watermark, marking the session dirty again immediately.
                coedit_channel.broadcast_yjs(
                    session_id, create_update_message(doc.get_update(pre_finalize))
                )
        return CheckpointOutcome(
            session_id=session_id,
            sha=result.sha,
            body=result.new_body,
            diverged=diverged,
        )
