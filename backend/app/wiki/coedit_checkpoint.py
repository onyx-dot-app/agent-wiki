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
from app.wiki import coedit, coedit_channel
from app.wiki import git as wiki_git
from app.wiki import notify as wiki_notify
from app.wiki.utils import author_string, commit_and_fan_out

log = logging.getLogger(__name__)


def _tip_is_ours(head_sha: str, session_id: int) -> bool:
    """True if the repo tip is *this* session's own checkpoint commit — it
    carries our ``Coedit-session`` trailer, which means nothing else has
    committed since, so the next checkpoint can amend it in place (5c)."""
    return f"Coedit-session: {session_id}" in wiki_git.commit_message_of(head_sha).splitlines()


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
    # Tag the commit with the session id so the next checkpoint recognizes this
    # tip as ours and amends it in place (collapse) rather than stacking (5c).
    trailers.append(f"Coedit-session: {session_id}")
    lines.append("")
    lines.extend(trailers)
    return "\n".join(lines)


def checkpoint_session(session_id: int) -> str | None:
    """Commit a dirty session's buffer to git; return the new sha (or None).

    Collapses a run of same-session checkpoints into one commit by amending the
    tip when it's still ours (5c); commits anew when an external commit broke the
    run, reconciling it via the gateway's 3-way + AI merge. No-op (returns None)
    when the session is gone, clean (``version == checkpointed_version``), or the
    merge collapses to the current HEAD. Idempotent: after a successful commit the
    session is marked clean.
    """
    sess = coedit.get_session(session_id)
    if sess is None or sess.version == sess.checkpointed_version:
        return None  # gone or nothing new to commit

    path = sess.path
    primary_id = coedit.last_op_author(session_id)
    author = _user(primary_id) if primary_id else None
    message = _commit_message(session_id, primary_author_id=primary_id)

    # Collapse (5c): if the repo tip is *this session's* last checkpoint (nothing
    # committed since), amend it in place so a run of checkpoints stays one
    # commit. If anything else committed — an agent, another session — the tip
    # isn't ours (or HEAD moves under the amend's CAS), so we commit anew through
    # the merge gateway, which starts a fresh run.
    head = wiki_git.head_sha()
    committed_body = sess.buffer_text
    new_sha: str | None = None

    # System-initiated write: the editors' write permission was already enforced
    # when they joined/POSTed ops, so skip the ACL gate; not "agent activity".
    with set_current_user(author):
        if head is not None and _tip_is_ours(head, session_id):
            try:
                amended = wiki_git.amend_head(
                    path, sess.buffer_text, message,
                    author=author_string(), expected_head=head,
                )
                # Amending needs no merge (nothing committed since our tip); run
                # the same post-write fan-out a normal commit would, unless the
                # buffer already matched the tip (amend_head returned it unchanged).
                if amended != head:
                    wiki_notify.after_doc_write(
                        path, amended, ChangeKind.EDIT, author_string(),
                        trigger_coedit_rebase=False,
                    )
                new_sha = amended
            except wiki_git.GitHeadMovedError:
                new_sha = None  # an external commit landed under us → new commit

        if new_sha is None:
            # New commit: first checkpoint of a run, or an external commit broke
            # the run. Reconcile any such commit via the gateway's 3-way + AI merge.
            base_body = wiki_git.read_file_opt(path, ref=sess.base_sha) if sess.base_sha else None
            change_kind = ChangeKind.EDIT if sess.base_sha else ChangeKind.CREATE
            result = commit_and_fan_out(
                path, sess.buffer_text, message,
                change_kind=change_kind, base_body=base_body, ai_merge=True,
                skip_acl=True, record_activity=False, trigger_coedit_rebase=False,
            )
            if result is None:
                # No-op merge (buffer already matches HEAD content). Mark clean
                # against current HEAD so we don't re-attempt this version forever.
                head2 = wiki_git.head_sha_for_path(path)
                if head2 is not None:
                    coedit.mark_checkpointed(session_id, base_sha=head2, version=sess.version)
                else:
                    log.warning(
                        "coedit checkpoint: no-op merge but no HEAD for %s (session %s); left dirty",
                        path, session_id,
                    )
                return None
            new_sha, committed_body = result.sha, result.new_body

    # Sync the committed content back into the buffer + advance base_sha /
    # checkpointed_version. When the merge folded in a concurrent agent commit,
    # committed_body differs from the buffer we started with, so participants get
    # a resync. If a human op raced in during the commit, the version CAS misses
    # (res is None) and we leave the session dirty for the next checkpoint — never
    # advancing base_sha past content the buffer doesn't have.
    res = coedit.rebase_onto(
        session_id,
        base_version=sess.version,
        merged_text=committed_body,
        new_base_sha=new_sha,
        checkpointed=True,
    )
    if res is None:
        log.info(
            "coedit checkpoint: concurrent op during commit of %s; reconciling next checkpoint",
            path,
        )
    elif res.changed:
        coedit_channel.broadcast_resync(session_id, res.session.version)
    return new_sha
