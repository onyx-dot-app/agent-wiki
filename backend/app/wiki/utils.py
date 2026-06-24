"""Shared validation and post-write glue for the doc-edit tools.

The fuzzy replacer (`app.wiki.edit.replace`) and broken-link checker
(`app.wiki.links`) are pure wiki primitives. This module is the tool-side
adapter: argument validation, optimistic-concurrency check, commit +
reindex + trigger fan-out, and assembling the result dict the model sees.
"""

from __future__ import annotations

import difflib
import logging
from collections.abc import Generator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import timedelta
from pathlib import Path
from typing import Any

from app.auth import current_user
from app.llm.agents import merge_conflict_update
from app.llm.agents.tools.errors import ToolError
from app.models.wiki import ChangeKind, CommitMaxRetriesError, CommitResult
from app.wiki import (
    agent_activity,
    filesystem,
    git as wiki_git,
    links,
    notify as wiki_notify,
)

log = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# Path validation                                                             #
# --------------------------------------------------------------------------- #


def validate_doc_path(raw_path: Any) -> str:
    """Normalize and validate a wiki-relative ``.md`` path.

    Raises ``ToolError`` on traversal, missing extension, or empty input.
    """
    if not isinstance(raw_path, str) or not raw_path.strip():
        raise ToolError("path is required")
    try:
        rel = filesystem.safe_rel_path(raw_path.strip())
    except ValueError as exc:
        raise ToolError(f"invalid path: {exc}")
    if not rel.endswith(".md"):
        raise ToolError("only .md files are supported")
    return rel


# --------------------------------------------------------------------------- #
# Optimistic concurrency                                                      #
# --------------------------------------------------------------------------- #


# Maximum retry attempts for the 3-way merge loop in ``commit_and_fan_out``.
# Each attempt may invoke an LLM merge fallback, so keep this small to bound
# LLM spend.
_MERGE_MAX_RETRIES = 3


def assert_base_sha(path: str, base_sha: str | None) -> dict[str, str] | None:
    """Optimistic-concurrency check shared by every write tool.

    Returns ``None`` when the check passes or is opted out of (no
    ``base_sha`` provided). Returns the ``stale_base`` error dict — the
    same shape every write tool returns — when ``base_sha`` no longer
    matches HEAD for ``path``.

    Used by both the chat agent and the MCP write surface so external
    agents can rebase against drift instead of clobbering. The check
    is a no-op when ``base_sha`` is None.
    """
    if base_sha is None:
        return None

    head_sha = wiki_git.head_sha_for_path(path)
    if base_sha == head_sha:
        return None
    return {
        "error": "stale_base",
        "base_sha": base_sha,
        "current_sha": head_sha or "",
        "message": (
            "the file has changed since base_sha; re-read with "
            "read_doc, re-derive the edit, and retry"
        ),
    }


# --------------------------------------------------------------------------- #
# Author / current user                                                       #
# --------------------------------------------------------------------------- #


_FALLBACK_AUTHOR = "AI Wiki Helper <ai-wiki-helper@local>"

# Git identity for connector-pushed ingestion commits — these have no human
# user, so without this they'd fall back to ``_FALLBACK_AUTHOR``. Bound via
# ``system_author(INGEST_AUTHOR)`` on the ingest path; the email is what
# callers filter on when counting ingestion commits (see git.count_commits_since).
INGEST_AUTHOR_EMAIL = "onyx-ingest@local"
INGEST_AUTHOR = f"Onyx Ingest <{INGEST_AUTHOR_EMAIL}>"

# Explicit git author for system-initiated commits that legitimately have no
# user in context (document ingestion from a connector, etc.). Bound by the
# ``system_author`` context manager; consulted by ``author_string`` before it
# degrades to ``_FALLBACK_AUTHOR``.
_system_author_var: ContextVar[str | None] = ContextVar("system_author", default=None)


@contextmanager
def system_author(identity: str) -> Generator[None, None, None]:
    """Attribute userless commits inside this block to ``identity`` instead of
    the generic ``_FALLBACK_AUTHOR``.

    ``identity`` is a full git author string (``"Name <email>"``). Used by
    background paths that have no human principal but a known non-human actor
    — e.g. ``"Onyx Ingest <onyx-ingest@local>"`` for connector pushes. A no-op
    when a user *is* bound: ``author_string`` credits the user in that case.
    """
    token = _system_author_var.set(identity)
    try:
        yield
    finally:
        _system_author_var.reset(token)


def author_string() -> str | None:
    """Git author for a wiki commit driven by an agent tool call.

    Resolves to ``"<user-display> via <agent-name> <email>"`` when both
    the current user and an agent identity are bound — so commits made
    via MCP credit the human and name the agent acting on their behalf
    (e.g. ``"Yuhong Sun via Claude Code <yuhong@onyx.app>"``). With only
    a user bound, drops the ``via`` clause. With no user, uses the
    ``system_author`` identity if one is bound (e.g. ingestion), else
    falls back to the legacy bot author so seed scripts and orphaned
    background paths still produce a valid commit. Direct UI/API edits
    set their own per-user author at the API seam (see ``app/api/wiki.py``).
    """
    user = _current_user_or_none()
    if user is None:
        return _system_author_var.get() or _FALLBACK_AUTHOR
    display = user.name or user.email
    agent_name = agent_activity.agent_name_var.get()
    if agent_name:
        return f"{display} via {agent_name} <{user.email}>"
    return f"{display} <{user.email}>"


# --------------------------------------------------------------------------- #
# Commit + side effects                                                       #
# --------------------------------------------------------------------------- #


def commit_and_fan_out(
    path: str,
    body: str,
    message: str,
    *,
    change_kind: ChangeKind,
    activity_ttl: timedelta | None = None,
    skip_acl: bool = False,
    base_body: str | None = None,
    ai_merge: bool = False,
    max_retries: int = _MERGE_MAX_RETRIES,
    record_activity: bool = True,
) -> CommitResult | None:
    """The single write gateway: commit ``body`` to ``path``, fan out to triggers.

    When ``base_body`` is ``None`` the body is committed as-is (new pages,
    full-body overwrites with nothing to reconcile against). When ``base_body``
    is supplied the commit is a read-modify-write: any concurrent change that
    landed since ``base_body`` was read is reconciled with a 3-way merge
    (``git merge-file``). A clean merge commits the merged result. A conflict
    git can't resolve is handed to an LLM merge when ``ai_merge=True`` (the
    agent write path), or raises ``GitMergeConflictError`` when not (human
    path → 409). The loop retries up to ``max_retries`` times when HEAD keeps
    moving during the merge step, then raises ``CommitMaxRetriesError``.

    Ref-lock races are handled transparently inside ``commit_file`` — this
    function never sees them.

    ``activity_ttl`` overrides the default 24h TTL on the resulting
    Active-agents row — write tools surface this through their
    ``expires_in_seconds`` argument so an agent can declare how long
    it expects to keep working.

    ``skip_acl=True`` bypasses the write-permission gate. Use only for
    system-initiated writes (e.g. document ingestion) where there is no
    human user in context whose permissions should be enforced.

    ``record_activity=False`` skips the Active-agents row. The human
    ``PUT /file`` path passes this — a person saving a page in the editor
    is not "agent activity" and shouldn't surface on the activity rail.

    Returns a ``CommitResult`` on commit; returns ``None`` only on the merge
    path when the merged result equals the current content (no-op).
    """
    # Permission gate: editing requires write on the existing page.
    # Creating a new page is always allowed for the calling user — they
    # become the owner via the seeding hook in ``after_doc_write``.
    if change_kind == ChangeKind.EDIT and not skip_acl:
        from app.auth import PermissionDenied, require_can

        try:
            require_can("write", path)
        except PermissionDenied as exc:
            raise ToolError(str(exc))

    if base_body is None:
        return _commit_resolved(
            path, body, message, change_kind, activity_ttl,
            old_body=_read_head_or_empty(path), record_activity=record_activity,
        )

    # Read-modify-write: 3-way merge against concurrent changes, retrying when
    # HEAD keeps moving between the merge and the commit.
    _base = base_body
    _new = body
    for attempt in range(max_retries + 1):
        head_sha = wiki_git.head_sha_for_path(path)
        # Read from HEAD, not the working tree: after a concurrent commit the
        # working tree may have stale content, so a filesystem read could make
        # the next merge a no-op that clobbers the winner's commit.
        current = _read_head_or_empty(path)
        if current != _base:
            mr = wiki_git.merge_content(_base, current, _new)
            if mr.clean:
                merged = mr.merged
            elif ai_merge:
                merged = merge_conflict_update.merge(
                    wiki_path=path,
                    base_body=_base,
                    current_body=current,
                    draft_body=_new,
                )
            else:
                raise wiki_git.GitMergeConflictError(path)
        else:
            merged = _new
        if merged == current:
            return None
        post_sha = wiki_git.head_sha_for_path(path)
        if post_sha == head_sha:
            try:
                return _commit_resolved(
                    path, merged, message, change_kind, activity_ttl,
                    old_body=current, record_activity=record_activity,
                    expected_head=head_sha,
                )
            except (wiki_git.GitNothingToCommitError, wiki_git.GitHeadMovedError):
                # A concurrent writer committed in the window between our
                # pre-commit SHA check and the locked commit. Re-read HEAD and
                # re-merge rather than committing stale content.
                if attempt >= max_retries:
                    raise CommitMaxRetriesError(
                        attempt, wiki_git.head_sha_for_path(path) or ""
                    )
                log.info(
                    "commit_and_fan_out: concurrent commit under %s, retrying (%d/%d)",
                    path, attempt + 1, max_retries,
                )
                _base = current
                _new = merged
                continue
        if attempt >= max_retries:
            raise CommitMaxRetriesError(attempt, post_sha or "")
        log.info(
            "commit_and_fan_out: HEAD moved for %s, retrying (%d/%d)",
            path, attempt + 1, max_retries,
        )
        _base = current
        _new = merged
    raise CommitMaxRetriesError(max_retries, "")  # unreachable


def _commit_resolved(
    path: str,
    body: str,
    message: str,
    change_kind: ChangeKind,
    activity_ttl: timedelta | None,
    *,
    old_body: str,
    record_activity: bool = True,
    expected_head: str | None = None,
) -> CommitResult:
    """Commit ``body``, record activity, and run the reindex + trigger fan-out.

    The leaf of ``commit_and_fan_out``: the body has already been resolved
    (post-merge), so this just commits it verbatim, records activity, and runs
    the reindex + trigger fan-out. Activity is DB-only.

    Activity is recorded only after ``commit_file`` returns: a terminal commit
    failure (e.g. ``GitHeadMovedError``) would otherwise leave a phantom
    "wrote" row on the activity rail for a commit that never landed.
    """
    user = _current_user_or_none()
    author = author_string()
    sha = wiki_git.commit_file(path, body, message, author=author, expected_head=expected_head)

    if user is not None and record_activity:
        agent_name = agent_activity.agent_name_var.get()
        # if a launcher session is driving this commit, override
        # agent_name with the manifest's tool_id so the UI attributes
        # the edit to "claude-code" / "codex" instead of nothing.
        from app.launchers.current_session import current_agent_session_id
        from app.db import agent_sessions as _sessions

        launcher_sid = current_agent_session_id()
        if launcher_sid is not None and agent_name is None:
            sess_row = _sessions.get(launcher_sid)
            if sess_row is not None:
                agent_name = sess_row["tool_id"]

        upsert_kwargs: dict[str, Any] = dict(
            user_id=user.id,
            agent_name=agent_name,
            doc_path=path,
            activity="wrote",
            description=message,
            agent_session_id=launcher_sid,
        )
        if activity_ttl is not None:
            upsert_kwargs["ttl"] = activity_ttl
        expires_at = agent_activity.upsert_activity(**upsert_kwargs)
        # Local import: avoids loading the tasks package at tool-load time.
        from app.tasks.agent_activity import schedule_cleanup_for_natural_key

        schedule_cleanup_for_natural_key(
            user_id=user.id,
            agent_name=agent_name,
            expires_at=expires_at,
        )

    wiki_notify.after_doc_write(
        path,
        sha,
        change_kind,
        author,
        owner_user_id=user.id if (user is not None and change_kind == ChangeKind.CREATE) else None,
    )
    return CommitResult(sha=sha, old_body=old_body, new_body=body)


def mark_doc_read(path: str) -> None:
    """Register a ``read`` activity row for the current user.

    No-op outside a request context (no current user). DB-only — the
    doc body is not touched.
    """
    user = _current_user_or_none()
    if user is None:
        return
    agent_name = agent_activity.agent_name_var.get()
    # derive agent_name from launcher session if not set.
    from app.launchers.current_session import current_agent_session_id
    from app.db import agent_sessions as _sessions

    launcher_sid = current_agent_session_id()
    if launcher_sid is not None and agent_name is None:
        sess_row = _sessions.get(launcher_sid)
        if sess_row is not None:
            agent_name = sess_row["tool_id"]

    expires_at = agent_activity.upsert_activity(
        user_id=user.id,
        agent_name=agent_name,
        doc_path=path,
        activity="read",
        description=None,
        agent_session_id=launcher_sid,
    )
    from app.tasks.agent_activity import schedule_cleanup_for_natural_key

    schedule_cleanup_for_natural_key(
        user_id=user.id,
        agent_name=agent_name,
        expires_at=expires_at,
    )


# --------------------------------------------------------------------------- #
# Argument parsing                                                            #
# --------------------------------------------------------------------------- #


# Bounds on the agent-supplied TTL override. 60s lower bound prevents
# trivially-short fires that would just churn the cleanup queue; 7-day
# upper bound caps how long a stale row can hang around if the agent
# disappears mid-task.
_EXPIRES_IN_MIN_SECONDS = 60
_EXPIRES_IN_MAX_SECONDS = 7 * 24 * 60 * 60


def parse_expires_in_seconds(raw: Any) -> timedelta | None:
    """Validate and convert the optional ``expires_in_seconds`` arg.

    Returns ``None`` when not provided; otherwise a ``timedelta``.
    Raises ``ToolError`` on invalid values so callers can return
    ``{"error": ...}`` to the model.
    """
    if raw is None:
        return None
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise ToolError("expires_in_seconds must be an integer when provided")
    if raw < _EXPIRES_IN_MIN_SECONDS or raw > _EXPIRES_IN_MAX_SECONDS:
        raise ToolError(
            f"expires_in_seconds must be between {_EXPIRES_IN_MIN_SECONDS} "
            f"and {_EXPIRES_IN_MAX_SECONDS}"
        )
    return timedelta(seconds=raw)


def _current_user_or_none():
    try:
        return current_user()
    except RuntimeError:
        return None


def read_existing(path: str) -> str:
    """Read the current body of ``path`` from the wiki working tree."""
    return Path(filesystem.absolute(path)).read_text()


def _read_head_or_empty(path: str) -> str:
    """Read ``path`` from the last git commit (HEAD), or ``""`` if not yet committed."""
    try:
        return wiki_git.read_file(path, ref="HEAD")
    except wiki_git.UnknownSha:
        return ""


def read_existing_or_empty(path: str) -> str:
    """Like ``read_existing`` but returns ``""`` when the file doesn't yet exist."""
    p = Path(filesystem.absolute(path))
    return p.read_text() if p.is_file() else ""


def file_exists(path: str) -> bool:
    return Path(filesystem.absolute(path)).is_file()


# --------------------------------------------------------------------------- #
# Result assembly                                                             #
# --------------------------------------------------------------------------- #


def unified_diff(old: str, new: str, path: str) -> str:
    diff_lines = difflib.unified_diff(
        old.splitlines(keepends=True),
        new.splitlines(keepends=True),
        fromfile=path,
        tofile=path,
    )
    return "".join(diff_lines)


def broken_links(path: str, body: str) -> list[dict[str, str]]:
    return [
        {"target": b.target, "resolved": b.resolved} for b in links.find_broken_links(body, path)
    ]
