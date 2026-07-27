"""Checkpoint a co-edit session's live Yjs doc back into git.

Rebuilds a throwaway ``pycrdt.Doc`` from the session's durable state —
``ydoc_snapshot`` (a binary snapshot at ``ydoc_snapshot_seq``) plus every
``coedit_updates`` row logged since — and commits through the *existing*
write gateway, reconciling any agent/ingest commit that landed meanwhile via
the same 3-way + AI merge. Durability is the update log + snapshot in
Postgres, so a checkpoint is about visibility (making the committed page
fresh for readers/search/agents) and bounding merge size — not data safety.

Deliberately never touches any process's live ``coedit_room.Room`` — the
whole point of rebuilding from (snapshot, updates) instead. That's what lets
this run as a plain ``coedit_queue`` task (``app/tasks/coedit_checkpoint.py``)
dispatched to any worker, not just the one process (if any) holding the
session's room live: a live room is thread-affine (PyO3-unsendable
``Doc``/``Awareness`` — see ``coedit_room.py``), so touching one from here,
on a worker's own thread, would be exactly the cross-thread violation this
rearchitecture exists to remove. A room that *is* live somewhere still needs
telling once a checkpoint lands elsewhere — see
``app/tasks/coedit_checkpoint.py``'s notify step.

Attribution: the commit author is the last editor (so git blame credits
whoever last touched the doc); the other session participants are added as
``Co-authored-by:`` trailers. See
``Engineering Projects/Agent Wiki Project/design/Co-Editing.md``.
"""

from __future__ import annotations

import logging

from pycrdt import Doc
from pydantic import BaseModel, ConfigDict

from app.auth import User, set_current_user
from app.auth import users as users_repo
from app.models.wiki import ChangeKind
from app.wiki import coedit, filesystem
from app.wiki import drafts as wiki_drafts
from app.wiki import git as wiki_git
from app.wiki import markdown_splice, markdown_yjs
from app.wiki.markdown_splice import TouchedTracker

log = logging.getLogger(__name__)


def _user(user_id: str) -> User | None:
    row = users_repo.get_by_id(user_id)
    if row is None:
        return None
    return User(
        id=row["id"], email=row["email"], name=row["name"], is_admin=bool(row["is_admin"])
    )


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
    update logged since — never touches any process's live room.

    ``base_body`` — the pre-replay text ``markdown_splice.checkpoint_body``
    diffs against — is read from git at ``sess.base_sha``, *not* reconstructed
    from the doc: ``advance_checkpoint`` always advances ``ydoc_snapshot`` and
    ``base_sha`` together, so the git blob at ``base_sha`` is always exactly
    the raw markdown ``ydoc_snapshot`` was seeded from (same invariant
    ``coedit_room.Room.__init__`` relies on, which stores the raw string it
    was seeded from directly rather than round-tripping through
    ``reconstruct_body``). Round-tripping through the doc instead would drift
    from the original formatting on every untouched block, defeating
    ``checkpoint_body``'s verbatim-slice diffing.

    The ``TouchedTracker`` is created right after the doc is seeded, before
    replay, so every replayed update is observed by the tracker exactly as
    it would be for a live edit (``TouchedTracker.observe_deep`` sees any
    mutation regardless of origin) — matching ``coedit_room.Room.__init__``'s
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
    base_body = wiki_git.read_file_opt(sess.path, sess.base_sha) if sess.base_sha else ""
    tracker = TouchedTracker(doc)
    since = coedit.updates_since(sess.id, sess.ydoc_snapshot_seq)
    for u in since.updates:
        doc.apply_update(u.update_payload)
    replayed_seq = since.head_seq if since.head_seq is not None else sess.ydoc_snapshot_seq
    return doc, base_body or "", tracker, replayed_seq


class CheckpointOutcome(BaseModel):
    """What a successful checkpoint produced — enough for the caller
    (``app/tasks/coedit_checkpoint.py``) to notify any process holding this
    session's room live, without this module knowing anything about the
    realtime bus (same domain/fan-out split as ``coedit_rebase.py`` vs.
    ``app/tasks/coedit_rebase.py``). ``sha`` is the checkpoint's own commit —
    also the session's new ``base_sha`` from here on, same value serving
    both purposes."""

    model_config = ConfigDict(frozen=True)

    session_id: int
    sha: str
    body: str


def checkpoint_session(session_id: int) -> CheckpointOutcome | None:
    """Commit a dirty session's doc to git; return the outcome (or None).

    Pure sync — no asyncio, no Doc access outside the throwaway one this
    function builds and discards. No-op when the session is gone, clean
    (``ydoc_seq == ydoc_checkpointed_seq``), has no snapshot yet (a session
    whose very first connection hasn't finished creating its room —
    momentary, see ``coedit.set_initial_snapshot``), or the merge collapses
    to the current HEAD.
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
        body = markdown_splice.checkpoint_body(base_body, doc, tracker)
        change_kind = ChangeKind.EDIT if sess.base_sha else ChangeKind.CREATE

        primary_id = coedit.last_update_author(session_id)
        author = _user(primary_id) if primary_id else None
        message = _commit_message(session_id, primary_author_id=primary_id)

        # Local import: app.wiki.utils (indirectly, via notify -> tasks) imports
        # back into this module through app.tasks.coedit_checkpoint, so a
        # module-level import here is circular.
        from app.wiki.utils import commit_and_fan_out

        # System-initiated write: editors' write permission was already
        # enforced when they joined/applied updates, so skip the ACL gate;
        # not "agent activity".
        with set_current_user(author):
            result = commit_and_fan_out(
                path,
                body,
                message,
                change_kind=change_kind,
                base_body=base_body if sess.base_sha else None,
                ai_merge=True,
                skip_acl=True,
                record_activity=False,
                # This is the session's own commit — don't fold it back into
                # the session as an inbound rebase (the notify step below
                # already reconciles any live room directly).
                trigger_coedit_rebase=False,
            )

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
                    "coedit checkpoint: no-op merge but no HEAD for %s (session %s); "
                    "left dirty",
                    path,
                    session_id,
                )
                return None
            coedit.advance_checkpoint(
                session_id, seq=replayed_seq, snapshot=doc.get_update(), base_sha=head
            )
            return None

        # If the AI/3-way merge changed the content beyond what this doc
        # held (a concurrent external commit folded in), the *committed*
        # result — not this doc's own state — is the correct new snapshot,
        # so any future replay starts from what's actually on disk.
        if result.new_body != body:
            snap_doc = markdown_yjs.seed_doc_from_markdown(result.new_body)
            snapshot = snap_doc.get_update()
        else:
            snapshot = doc.get_update()

        coedit.advance_checkpoint(
            session_id, seq=replayed_seq, snapshot=snapshot, base_sha=result.sha
        )
        wiki_drafts.clear_if_diverged(path, result.new_body)
        return CheckpointOutcome(session_id=session_id, sha=result.sha, body=result.new_body)
