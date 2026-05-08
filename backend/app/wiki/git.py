"""Thin wrapper around the ``git`` CLI for the wiki repo.

The backend keeps the wiki as a real git repository on disk and shells out to
git via subprocess — no library dependency. All writes commit immediately so
history is always consistent with the working tree.
"""
from __future__ import annotations

import logging
import subprocess
from pathlib import Path

from app.config import CONFIG

log = logging.getLogger(__name__)


def _run(args: list[str], cwd: str | None = None, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=cwd or CONFIG.wiki_dir,
        check=check,
        capture_output=True,
        text=True,
    )


def ensure_wiki_repo() -> None:
    """Initialize the wiki git repo if it doesn't already exist.

    If the working tree already has files (e.g. seeded content from
    ``local_data/wiki`` in local dev), commit them as the initial revision
    so ``read_file``/``list_paths``/``history`` see them.
    """
    p = Path(CONFIG.wiki_dir)
    p.mkdir(parents=True, exist_ok=True)
    if (p / ".git").exists():
        return
    log.info("initializing wiki git repo at %s", p)
    _run(["init", "-b", "main"], cwd=str(p))
    _run(["config", "user.email", "agent-wiki@local"], cwd=str(p))
    _run(["config", "user.name", "agent-wiki"], cwd=str(p))
    _run(["add", "-A"], cwd=str(p))
    if _run(["diff", "--cached", "--quiet"], cwd=str(p), check=False).returncode != 0:
        _run(["commit", "-m", "Seed wiki from working tree"], cwd=str(p))
        log.info("seeded wiki repo with initial commit")


def commit_file(rel_path: str, body: str, message: str, author: str | None = None) -> str:
    """Write a file at ``rel_path`` (relative to the wiki root), commit, return SHA."""
    full = Path(CONFIG.wiki_dir) / rel_path
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(body)
    _run(["add", rel_path])
    if _run(["diff", "--cached", "--quiet"], check=False).returncode == 0:
        sha = _run(["rev-parse", "HEAD"]).stdout.strip()
        log.debug("commit_file no-op (no diff) %s sha=%s", rel_path, sha[:8])
        return sha
    env_args = ["--author", author] if author else []
    _run(["commit", "-m", message, *env_args])
    sha = _run(["rev-parse", "HEAD"]).stdout.strip()
    log.debug("commit_file %s sha=%s author=%s", rel_path, sha[:8], author or "default")
    return sha


def move_and_commit(
    old_rel_path: str,
    new_rel_path: str,
    body: str,
    message: str,
    author: str | None = None,
) -> str:
    """Rename a tracked file (delete old + write new) in one commit.

    Single-commit moves let ``git log --follow`` trace history across renames.
    """
    full_new = Path(CONFIG.wiki_dir) / new_rel_path
    full_new.parent.mkdir(parents=True, exist_ok=True)
    full_new.write_text(body)
    _run(["add", new_rel_path])
    _run(["rm", "--", old_rel_path])
    env_args = ["--author", author] if author else []
    _run(["commit", "-m", message, *env_args])
    sha = _run(["rev-parse", "HEAD"]).stdout.strip()
    log.debug("move_and_commit %s -> %s sha=%s", old_rel_path, new_rel_path, sha[:8])
    return sha


def move_path(
    old_rel_path: str,
    new_rel_path: str,
    message: str,
    author: str | None = None,
) -> tuple[str, list[tuple[str, str]]]:
    """Rename a tracked file or directory via ``git mv``, single commit.

    Returns ``(sha, [(old, new), ...])`` where each tuple is one tracked
    file that was actually moved. For a directory rename this lists every
    nested file. Used by tools that move things without rewriting content.
    """
    listed = _run(["ls-files", "--", old_rel_path]).stdout.splitlines()
    tracked = [p for p in listed if p]
    moves: list[tuple[str, str]] = []
    for old_p in tracked:
        if old_p == old_rel_path:
            moves.append((old_p, new_rel_path))
        else:
            rest = old_p[len(old_rel_path):].lstrip("/")
            moves.append((old_p, f"{new_rel_path}/{rest}"))
    full_new = Path(CONFIG.wiki_dir) / new_rel_path
    full_new.parent.mkdir(parents=True, exist_ok=True)
    _run(["mv", old_rel_path, new_rel_path])
    env_args = ["--author", author] if author else []
    _run(["commit", "-m", message, *env_args])
    sha = _run(["rev-parse", "HEAD"]).stdout.strip()
    log.debug(
        "move_path %s -> %s sha=%s files=%d",
        old_rel_path,
        new_rel_path,
        sha[:8],
        len(moves),
    )
    return sha, moves


def delete_path(rel_path: str, message: str, author: str | None = None) -> str:
    """Remove a tracked file or directory (recursively) and commit. Returns SHA."""
    _run(["rm", "-r", "--", rel_path])
    env_args = ["--author", author] if author else []
    _run(["commit", "-m", message, *env_args])
    sha = _run(["rev-parse", "HEAD"]).stdout.strip()
    log.debug("delete_path %s sha=%s author=%s", rel_path, sha[:8], author or "default")
    return sha


def read_file(rel_path: str, ref: str = "HEAD") -> str:
    """Read file contents at a given git ref. Default: working tree's last commit."""
    return _run(["show", f"{ref}:{rel_path}"]).stdout


def history(rel_path: str, limit: int = 100) -> list[dict]:
    """Return commit metadata (incl. body) for a path, newest first."""
    sep_field = "\x1f"
    sep_record = "\x1e"
    fmt = f"%H{sep_field}%an{sep_field}%aI{sep_field}%s{sep_field}%b{sep_record}"
    out = _run(
        ["log", f"-n{limit}", "--follow", f"--pretty=format:{fmt}", "--", rel_path]
    ).stdout
    rows: list[dict] = []
    for record in out.split(sep_record):
        record = record.strip("\n")
        if not record:
            continue
        parts = record.split(sep_field, 4)
        if len(parts) < 5:
            continue
        sha, author, iso, subject, body = parts
        rows.append({
            "sha": sha,
            "author": author,
            "ts": iso,
            "message": subject,
            "body": body,
        })
    return rows


def head_sha_for_path(rel_path: str) -> str | None:
    """SHA of the most recent commit that touched ``rel_path``, or None."""
    out = _run(
        ["log", "-n1", "--pretty=format:%H", "--", rel_path], check=False
    ).stdout.strip()
    return out or None


def commits_between(base_sha: str, head_sha: str, rel_path: str) -> list[str]:
    """SHAs reachable from head_sha but not base_sha that touched rel_path,
    newest first. Excludes base_sha itself."""
    out = _run(
        ["log", "--pretty=format:%H", f"{base_sha}..{head_sha}", "--", rel_path],
        check=False,
    ).stdout
    return [s for s in out.splitlines() if s]


def list_paths(prefix: str = "") -> list[str]:
    """List tracked files under a path prefix."""
    out = _run(["ls-files", prefix or "."]).stdout
    return [line for line in out.splitlines() if line]


def paths_changed_in(sha: str) -> list[str]:
    """File paths touched by a single commit. Empty list if sha is unknown."""
    out = _run(
        ["diff-tree", "--no-commit-id", "--name-only", "-r", sha], check=False
    ).stdout
    return [line for line in out.splitlines() if line]


def tree_paths_at(sha: str) -> list[str]:
    """All tracked file paths in the tree at ``sha``."""
    out = _run(["ls-tree", "-r", "--name-only", sha], check=False).stdout
    return [line for line in out.splitlines() if line]


def diff_for_commit(sha: str, rel_path: str | None = None) -> str:
    args = ["show", "--no-color", sha]
    if rel_path:
        args += ["--", rel_path]
    return _run(args).stdout
