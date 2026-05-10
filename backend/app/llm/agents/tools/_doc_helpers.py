"""Shared validation and post-write glue for the doc-edit tools.

The fuzzy replacer (`app.wiki.edit.replace`) and broken-link checker
(`app.wiki.links`) are pure wiki primitives. This module is the tool-side
adapter: argument validation, read-before-write enforcement, commit +
reindex + trigger fan-out, and assembling the result dict the model sees.
"""
from __future__ import annotations

import difflib
from pathlib import Path
from typing import Any

from app.auth import current_user
from app.llm.agents._session import seen_doc_paths
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
# Read-before-write                                                           #
# --------------------------------------------------------------------------- #


def assert_base_sha(rel: str, base_sha: str | None) -> dict[str, str] | None:
    """Optimistic-concurrency check shared by every write tool.

    Returns ``None`` when the check passes or is opted out of (no
    ``base_sha`` provided). Returns the ``stale_base`` error dict — the
    same shape every write tool returns — when ``base_sha`` no longer
    matches HEAD for ``rel``.

    Used by the MCP write surface (Phase 4 in
    ``local_data/wiki/mcp-server/mcp-server.md``) so external agents
    can rebase against drift instead of clobbering. The chat agent
    holds direct loop state and rarely passes ``base_sha``; that's
    fine — both flows go through the same handler, the check is a
    no-op when ``base_sha`` is None.
    """
    if base_sha is None:
        return None
    from app.wiki import git as wiki_git

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


def assert_read_before_write(rel: str) -> None:
    """Refuse to edit an existing doc the model has not seen this turn.

    A no-op when called outside a chat loop (``seen_doc_paths`` is the
    default ``None``) or when the file does not yet exist.
    """
    seen = seen_doc_paths.get()
    if seen is None:
        return
    abs_path = filesystem.absolute(rel)
    if not Path(abs_path).is_file():
        return
    if rel in seen:
        return
    raise ToolError(
        f"You must call read_page({rel!r}) before editing it. Searching alone "
        "isn't enough — search_wiki returns short snippets, not full bodies. "
        "This protects against blind overwrites."
    )


# --------------------------------------------------------------------------- #
# Author / current user                                                       #
# --------------------------------------------------------------------------- #


def author_string() -> str | None:
    """Git author for any wiki commit driven by the chat agent's tools.

    Chat-flow writes are attributed to a bot identity rather than the
    prompting user — the user didn't author the diff, the agent did on
    their behalf. Direct UI/API edits keep their own per-user attribution
    (see ``app/api/triggers.py:_git_author``).
    """
    return "AI Wiki Helper <ai-wiki-helper@local>"


# --------------------------------------------------------------------------- #
# Commit + side effects                                                       #
# --------------------------------------------------------------------------- #


def commit_and_fan_out(
    rel: str, body: str, message: str, *, change_kind: str
) -> str:
    """Commit ``body`` to ``rel``, queue reindex, fan out to triggers.

    Returns the commit SHA. ``change_kind`` is ``"create"`` or ``"edit"``.

    Side effects:
      1. Permission gate (write on existing pages).
      2. Register a ``wrote`` activity row for the current user.
      3. Commit, then run the standard reindex + trigger fan-out.

    Activity is DB-only — the doc body is committed verbatim.
    """
    # Permission gate: editing requires write on the existing page.
    # Creating a new page is always allowed for the calling user — they
    # become the owner via the seeding hook in ``after_doc_write``.
    if change_kind == "edit":
        from app.auth import PermissionDenied, require_can

        try:
            require_can("write", rel)
        except PermissionDenied as exc:
            raise ToolError(str(exc))

    user = _current_user_or_none()
    if user is not None:
        agent_name = agent_activity.agent_name_var.get()
        expires_at = agent_activity.upsert_activity(
            user_id=user.id,
            agent_name=agent_name,
            doc_path=rel,
            activity="wrote",
            description=message,
        )
        # Local import: avoids loading the tasks package at tool-load time.
        from app.tasks.agent_activity import schedule_cleanup_for_natural_key
        schedule_cleanup_for_natural_key(
            user_id=user.id,
            agent_name=agent_name,
            doc_path=rel,
            activity="wrote",
            expires_at=expires_at,
        )

    author = author_string()
    sha = wiki_git.commit_file(rel, body, message, author=author)
    wiki_notify.after_doc_write(
        rel, sha, change_kind, author,
        owner_user_id=user.id if (user is not None and change_kind == "create") else None,
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
    expires_at = agent_activity.upsert_activity(
        user_id=user.id,
        agent_name=agent_name,
        doc_path=rel,
        activity="read",
        description=None,
    )
    from app.tasks.agent_activity import schedule_cleanup_for_natural_key
    schedule_cleanup_for_natural_key(
        user_id=user.id,
        agent_name=agent_name,
        doc_path=rel,
        activity="read",
        expires_at=expires_at,
    )


def _current_user_or_none():
    try:
        return current_user()
    except RuntimeError:
        return None


def read_existing(rel: str) -> str:
    """Read the current body of ``rel`` from the wiki working tree."""
    return Path(filesystem.absolute(rel)).read_text()


def file_exists(rel: str) -> bool:
    return Path(filesystem.absolute(rel)).is_file()


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
        {"target": b.target, "resolved": b.resolved}
        for b in links.find_broken_links(body, rel)
    ]
