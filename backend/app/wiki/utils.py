"""Shared validation and post-write glue for the doc-edit tools.

The fuzzy replacer (`app.wiki.edit.replace`) and broken-link checker
(`app.wiki.links`) are pure wiki primitives. This module is the tool-side
adapter: argument validation, optimistic-concurrency check, commit +
reindex + trigger fan-out, and assembling the result dict the model sees.
"""

from __future__ import annotations

import difflib
import logging
from datetime import timedelta
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

from app.auth import current_user
from app.llm.agents import merge_conflict_update
from app.models.wiki import AiRebaseMaxRetriesException, ChangeKind, CommitResult
from app.wiki import (
    agent_activity,
    filesystem,
    git as wiki_git,
    links,
    notify as wiki_notify,
)


class ToolError(Exception):
    """Tool input was invalid or a precondition failed.

    The handler catches this and returns ``{"error": str(exc)}`` to the
    model instead of raising. Use for user-facing error messages — the
    string is shown to the LLM verbatim.
    """


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


# Maximum retry attempts inside ``commit_with_ai_rebase`` — each attempt is a
# full ``generate_body`` call, so keep this small to bound LLM spend.
_AI_REBASE_MAX_RETRIES = 3


def commit_with_ai_rebase(
    wiki_path: str,
    message: str,
    *,
    base_body: str,
    new_body: str,
    max_retries: int = _AI_REBASE_MAX_RETRIES,
    activity_ttl: timedelta | None = None,
) -> CommitResult | None:
    """Commit with 3-way merge retry when HEAD moves between read and commit.

    Caller provides ``base_body`` (the content the edit was derived from)
    and ``new_body`` (the desired result). If HEAD has advanced since
    ``base_body`` was read, a 3-way merge reconciles the two change sets
    (git merge-file first, LLM fallback on conflict). The loop retries up
    to ``max_retries`` times when HEAD keeps moving during the merge step.

    Returns ``None`` when the merged result equals the current content.
    Raises ``AiRebaseMaxRetriesException`` when the retry limit is hit.
    Any ``LLMError`` raised by the merge fallback propagates immediately.
    """
    _base = base_body
    _new = new_body
    for attempt in range(max_retries + 1):
        head_sha = wiki_git.head_sha_for_path(wiki_path)
        current = read_existing_or_empty(wiki_path)
        if current != _base:
            mr = wiki_git.merge_content(_base, current, _new)
            if mr.clean:
                merged = mr.merged
            else:
                merged = merge_conflict_update.merge(
                    wiki_path=wiki_path,
                    base_body=_base,
                    current_body=current,
                    draft_body=_new,
                )
        else:
            merged = _new
        if merged == current:
            return None
        post_sha = wiki_git.head_sha_for_path(wiki_path)
        if post_sha == head_sha:
            sha = commit_and_fan_out(
                wiki_path, merged, message,
                change_kind=ChangeKind.EDIT, activity_ttl=activity_ttl,
            )
            return CommitResult(sha=sha, old_body=current, new_body=merged)
        if attempt >= max_retries:
            raise AiRebaseMaxRetriesException(attempt, post_sha or "")
        log.info(
            "commit_with_ai_rebase: HEAD moved for %s, retrying (%d/%d)",
            wiki_path, attempt + 1, max_retries,
        )
        _base = current
        _new = merged
    raise AiRebaseMaxRetriesException(max_retries, "")  # unreachable


def assert_base_sha(rel: str, base_sha: str | None) -> dict[str, str] | None:
    """Optimistic-concurrency check shared by every write tool.

    Returns ``None`` when the check passes or is opted out of (no
    ``base_sha`` provided). Returns the ``stale_base`` error dict — the
    same shape every write tool returns — when ``base_sha`` no longer
    matches HEAD for ``rel``.

    Used by both the chat agent and the MCP write surface so external
    agents can rebase against drift instead of clobbering. The check
    is a no-op when ``base_sha`` is None.
    """
    if base_sha is None:
        return None

    head_sha = wiki_git.head_sha_for_path(rel)
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


def author_string() -> str | None:
    """Git author for a wiki commit driven by an agent tool call.

    Resolves to ``"<user-display> via <agent-name> <email>"`` when both
    the current user and an agent identity are bound — so commits made
    via MCP credit the human and name the agent acting on their behalf
    (e.g. ``"Yuhong Sun via Claude Code <yuhong@onyx.app>"``). With only
    a user bound, drops the ``via`` clause; with neither, falls back to
    the legacy bot author so seed scripts and orphaned background paths
    still produce a valid commit. Direct UI/API edits set their own
    per-user author at the API seam (see ``app/api/wiki.py``).
    """
    user = _current_user_or_none()
    if user is None:
        return _FALLBACK_AUTHOR
    display = user.name or user.email
    agent_name = agent_activity.agent_name_var.get()
    if agent_name:
        return f"{display} via {agent_name} <{user.email}>"
    return f"{display} <{user.email}>"


# --------------------------------------------------------------------------- #
# Commit + side effects                                                       #
# --------------------------------------------------------------------------- #


def commit_and_fan_out(
    rel: str,
    body: str,
    message: str,
    *,
    change_kind: ChangeKind,
    activity_ttl: timedelta | None = None,
) -> str:
    """Commit ``body`` to ``rel``, queue reindex, fan out to triggers.

    Returns the commit SHA.

    ``activity_ttl`` overrides the default 24h TTL on the resulting
    Active-agents row — write tools surface this through their
    ``expires_in_seconds`` argument so an agent can declare how long
    it expects to keep working.

    Side effects:
      1. Permission gate (write on existing pages).
      2. Register a ``wrote`` activity row for the current user.
      3. Commit, then run the standard reindex + trigger fan-out.

    Activity is DB-only — the doc body is committed verbatim.
    """
    # Permission gate: editing requires write on the existing page.
    # Creating a new page is always allowed for the calling user — they
    # become the owner via the seeding hook in ``after_doc_write``.
    if change_kind == ChangeKind.EDIT:
        from app.auth import PermissionDenied, require_can

        try:
            require_can("write", rel)
        except PermissionDenied as exc:
            raise ToolError(str(exc))

    user = _current_user_or_none()
    if user is not None:
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
            doc_path=rel,
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

    author = author_string()
    sha = wiki_git.commit_file(rel, body, message, author=author)
    wiki_notify.after_doc_write(
        rel,
        sha,
        change_kind,
        author,
        owner_user_id=user.id if (user is not None and change_kind == ChangeKind.CREATE) else None,
    )
    return sha


def mark_doc_read(rel: str) -> None:
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
        doc_path=rel,
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


def read_existing(wiki_path: str) -> str:
    """Read the current body of ``wiki_path`` from the wiki working tree."""
    return Path(filesystem.absolute(wiki_path)).read_text()


def read_existing_or_empty(wiki_path: str) -> str:
    """Like ``read_existing`` but returns ``""`` when the file doesn't yet exist."""
    p = Path(filesystem.absolute(wiki_path))
    return p.read_text() if p.is_file() else ""


def file_exists(wiki_path: str) -> bool:
    return Path(filesystem.absolute(wiki_path)).is_file()


# --------------------------------------------------------------------------- #
# Result assembly                                                             #
# --------------------------------------------------------------------------- #


def unified_diff(old: str, new: str, rel: str) -> str:
    diff_lines = difflib.unified_diff(
        old.splitlines(keepends=True),
        new.splitlines(keepends=True),
        fromfile=rel,
        tofile=rel,
    )
    return "".join(diff_lines)


def broken_links(rel: str, body: str) -> list[dict[str, str]]:
    return [
        {"target": b.target, "resolved": b.resolved} for b in links.find_broken_links(body, rel)
    ]
