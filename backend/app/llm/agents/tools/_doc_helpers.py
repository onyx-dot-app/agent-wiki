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
    """Best-effort author derived from the current request's user.

    Tools usually run inside ``stream_with_context`` so the Flask app
    context is available. Outside that (tests, periodic tasks invoking
    the same helpers) we silently fall back to ``None`` and let the git
    wrapper use its default identity.
    """
    try:
        user = current_user()
    except RuntimeError:
        return None
    if user is None:
        return None
    return f"{user.name or user.email} <{user.email}>"


# --------------------------------------------------------------------------- #
# Commit + side effects                                                       #
# --------------------------------------------------------------------------- #


def commit_and_fan_out(
    rel: str, body: str, message: str, *, change_kind: str
) -> str:
    """Commit ``body`` to ``rel``, queue reindex, fan out to triggers.

    Returns the commit SHA. ``change_kind`` is ``"create"`` or ``"edit"``.

    Side effects (in order):
      1. Reject the write if the agent's body altered the registry-managed
         ``agents:`` frontmatter block.
      2. Register a ``wrote`` activity for the current user (if any).
      3. Strip and re-render the ``agents:`` frontmatter from current DB
         state so the committed body reflects the new registration.
      4. Commit, then run the standard reindex + trigger fan-out.
    """
    current_disk_body = read_existing(rel) if file_exists(rel) else ""
    try:
        agent_activity.assert_frontmatter_unchanged(
            incoming_body=body, current_disk_body=current_disk_body
        )
    except agent_activity.FrontmatterTamperedError as exc:
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

    body = agent_activity.replace_frontmatter(body, rel)

    author = author_string()
    sha = wiki_git.commit_file(rel, body, message, author=author)
    wiki_notify.after_doc_write(rel, sha, change_kind, author)
    return sha


def mark_doc_read(rel: str) -> None:
    """Register a ``read`` activity for the current user and refresh the
    doc's frontmatter to reflect it.

    No-op outside a request context (no current user). The frontmatter
    refresh commits via the registry's frontmatter-only path, which
    bypasses trigger fan-out (the content didn't change).
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
    refresh_doc_frontmatter(rel, message=f"agent-activity: refresh {rel}")


def refresh_doc_frontmatter(rel: str, *, message: str) -> str | None:
    """Re-render and commit the ``agents:`` frontmatter for ``rel``.

    Skipped (returns None) if the file isn't tracked yet — the registry
    only attaches metadata to existing docs. Bypasses trigger fan-out
    because nothing in the content changed; the FTS reindex still runs
    so search hits stay consistent.
    """
    if not file_exists(rel):
        return None
    old_body = read_existing(rel)
    new_body = agent_activity.replace_frontmatter(old_body, rel)
    if new_body == old_body:
        return None
    author = author_string()
    sha = wiki_git.commit_file(rel, new_body, message, author=author)
    # Reindex only — this is a frontmatter-only commit and shouldn't fire
    # any natural-language triggers attached to the doc.
    from app.tasks.reindex import reindex_path  # local import: avoids cycle
    reindex_path(rel)
    return sha


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
