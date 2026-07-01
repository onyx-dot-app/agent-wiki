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
from app.wiki import coedit
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
    """
    sess = coedit.get_session(session_id)
    if sess is None or sess.version == sess.checkpointed_version:
        return None  # gone or nothing new to commit

    path = sess.path
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
        )

    if result is not None:
        coedit.mark_checkpointed(session_id, base_sha=result.sha, version=sess.version)
        return result.sha

    # None = the merge produced exactly the current HEAD (buffer already matches
    # committed content). Still mark clean against current HEAD so we don't
    # re-attempt this version forever.
    head = wiki_git.head_sha_for_path(path)
    if head is not None:
        coedit.mark_checkpointed(session_id, base_sha=head, version=sess.version)
    return None
