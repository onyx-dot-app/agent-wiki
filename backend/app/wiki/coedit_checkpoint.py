"""Checkpoint a co-edit session's live buffer back into git.

The live buffer lives in Postgres (``coedit_sessions.buffer_text``); a checkpoint
is the Layer-2 boundary that commits it to git through the *existing* write
gateway, reconciling any agent/ingest commit that landed meanwhile via the same
3-way + AI merge. Durability is Postgres, so a checkpoint is about visibility
(making the committed page fresh for readers/search/agents) and bounding merge
size — not data safety.

Attribution: the commit author is the last editor (so git blame credits whoever
last touched the buffer); the other session participants are added as
``Co-authored-by:`` trailers. See
``Engineering Projects/Agent Wiki Project/design/Co-Editing.md``.

This module is the engine (``checkpoint_session``). What *triggers* it — a
periodic scan, last-participant-leave, an explicit save — is wired separately.
"""

from __future__ import annotations

import logging

from app.auth import User, set_current_user
from app.auth import users as users_repo
from app.models.wiki import ChangeKind
from app.wiki import coedit, coedit_channel, filesystem
from app.wiki import drafts as wiki_drafts
from app.wiki import git as wiki_git
from app.wiki.utils import commit_and_fan_out

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


def checkpoint_session(session_id: int) -> str | None:
    """Commit a dirty session's buffer to git; return the new sha (or None).

    No-op (returns None) when the session is gone, clean
    (``version == checkpointed_version``), or the merge collapses to the current
    HEAD. Reconciles concurrent agent/ingest commits via the gateway's 3-way +
    AI merge. Idempotent: after a successful commit the session is marked clean.

    Serialized per session by an advisory lock, so two workers that both dequeued
    a checkpoint for the same session can't both commit the buffer — the loser
    blocks, then re-reads a clean/closed session and no-ops (see
    ``coedit.checkpoint_lock``). Distinct sessions still checkpoint in parallel.
    """
    with coedit.checkpoint_lock(session_id) as acquired:
        if not acquired:
            # Another worker is checkpointing this session and held the lock past
            # the wait cap. Skip — the periodic scan re-enqueues if still dirty.
            log.info("coedit checkpoint: session %s busy; skipping (scan retries)", session_id)
            return None
        return _checkpoint_locked(session_id)


def _checkpoint_locked(session_id: int) -> str | None:
    """Body of ``checkpoint_session``, run while holding the session's checkpoint
    advisory lock so the read-guard-commit sequence is atomic across workers."""
    sess = coedit.get_session(session_id)
    if sess is None:
        return None  # gone
    if sess.status != coedit.SessionStatus.ACTIVE.value:
        # A closed session is finalized — never re-commit it. This dedupes
        # queued duplicates: once the first checkpoint commits and closes the
        # session, every other queued copy no-ops here. A closed session should
        # already be clean (close follows a clean checkpoint); if it's somehow
        # dirty, log and skip rather than clobber HEAD with its stale buffer —
        # the edits stay in the buffer for manual recovery.
        if sess.version != sess.checkpointed_version:
            log.warning(
                "coedit checkpoint: session %s is %s but dirty (v%d != "
                "checkpointed v%d); skipping — buffer left uncommitted, needs "
                "manual reconciliation",
                session_id,
                sess.status,
                sess.version,
                sess.checkpointed_version,
            )
        return None
    if sess.version == sess.checkpointed_version:
        return None  # nothing new to commit

    path = sess.path
    # The page existed when the session was seeded (base_sha set) but the
    # working-tree file is gone — it was moved or deleted underneath the
    # session. A move should have re-keyed the session
    # (``coedit.on_path_moved``); committing here would resurrect the dead
    # path from the buffer. Working-tree ``is_file`` (not a git read) so a
    # transient git failure can't masquerade as a missing page; an OSError
    # propagates and the task retries. Participant-less sessions close (the
    # zombie case; buffer stays in the row for recovery). Sessions with live
    # participants just skip: if a move re-key is landing concurrently, the
    # scan retries after it points the session at the new path.
    if sess.base_sha is not None and not filesystem.absolute(path).is_file():
        if coedit.list_participants(session_id):
            log.warning(
                "coedit checkpoint: session %s targets missing path %r but has "
                "participants; skipping (scan retries after any move re-key)",
                session_id,
                path,
            )
            return None
        log.warning(
            "coedit checkpoint: session %s targets missing path %r (moved or "
            "deleted); closing — buffer left uncommitted",
            session_id,
            path,
        )
        coedit.close_session(session_id)
        return None
    # Merge base = the page content at the last checkpoint. None when the
    # session was opened on a not-yet-existing page (→ create).
    base_body = wiki_git.read_file_opt(path, ref=sess.base_sha) if sess.base_sha else None
    change_kind = ChangeKind.EDIT if sess.base_sha else ChangeKind.CREATE

    primary_id = coedit.last_op_author(session_id)
    author = _user(primary_id) if primary_id else None
    message = _commit_message(session_id, primary_author_id=primary_id)

    # System-initiated write: the editors' write permission was already enforced
    # when they joined/POSTed ops, so skip the ACL gate; not "agent activity".
    with set_current_user(author):
        result = commit_and_fan_out(
            path,
            sess.buffer_text,
            message,
            change_kind=change_kind,
            base_body=base_body,
            ai_merge=True,
            skip_acl=True,
            record_activity=False,
            # This is the session's own commit — don't fold it back into the
            # session as an inbound rebase (we sync the merged result below).
            trigger_coedit_rebase=False,
        )

    if result is not None:
        # Sync the committed content back into the buffer. When the commit-time
        # 3-way merge folded in a concurrent agent/ingest commit, result.new_body
        # differs from the buffer we committed; writing it back (and broadcasting
        # the delta) keeps the live buffer == git and stops a later checkpoint
        # from re-committing the pre-merge buffer and dropping the agent's edit.
        res = coedit.rebase_onto(
            session_id,
            base_version=sess.version,
            merged_text=result.new_body,
            new_base_sha=result.sha,
            checkpointed=True,
        )
        if res is None and result.new_body == sess.buffer_text:
            # A human op raced in during the commit — the buffer moved past what
            # we committed, so the write-back CAS missed. Nothing foreign was
            # folded in, so the commit *is* the buffer at ``sess.version``, and
            # recording that is simply true: the session stays dirty for the ops
            # that landed after it, and the next checkpoint merges from the sha we
            # just wrote.
            #
            # Skipping this is what made a busy session never converge. Typing
            # through a commit is the ordinary case, not an edge case, so the
            # watermark would stay behind forever: ``last_checkpoint_at`` never
            # advanced, leaving the session permanently overdue for the periodic
            # scan, and ``base_sha`` stayed pinned so every later checkpoint
            # 3-way merged a moving buffer against an ever-older base — which
            # duplicates and drops text.
            coedit.mark_checkpointed(session_id, base_sha=result.sha, version=sess.version)
        elif res is None:
            # Same race, but the commit-time merge folded in a concurrent
            # agent/ingest commit that never reached the buffer. Leave base_sha
            # where it is so the next checkpoint merges base=old, current=HEAD,
            # incoming=newer buffer and keeps that edit; advancing it would make
            # base == current and drop it.
            log.info(
                "coedit checkpoint: concurrent op during commit of %s; reconciling next checkpoint",
                path,
            )
        elif res.changed:
            # The commit-time merge folded in a concurrent agent commit; tell
            # participants to reload the merged buffer.
            coedit_channel.broadcast_resync(session_id, res.session.version)
        # Clear the template-drafting row once the committed body diverges from
        # the snapshot — the page is now the user's own, so the chat banner and
        # the template's system-prompt override should no longer apply.
        wiki_drafts.clear_if_diverged(path, result.new_body)
        return result.sha

    # None = the merge produced exactly the current HEAD (buffer already matches
    # committed content). Still mark clean against current HEAD so we don't
    # re-attempt this version forever.
    head = wiki_git.head_sha_for_path(path)
    if head is not None:
        coedit.mark_checkpointed(session_id, base_sha=head, version=sess.version)
    else:
        # Shouldn't happen — a no-op merge implies HEAD exists. Surface it rather
        # than silently leaving the session dirty and re-entering this path on
        # every future trigger.
        log.warning(
            "coedit checkpoint: no-op merge but no HEAD for %s (session %s); left dirty",
            path,
            session_id,
        )
    return None
