"""Thin wrapper around the ``git`` CLI for the wiki repo.

The backend keeps the wiki as a real git repository on disk and shells out to
git via subprocess — no library dependency. All writes commit immediately so
history is always consistent with the working tree.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from app.config import CONFIG


def _run(args: list[str], cwd: str | None = None, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=cwd or CONFIG.wiki_dir,
        check=check,
        capture_output=True,
        text=True,
    )


def ensure_wiki_repo() -> None:
    """Initialize the wiki git repo if it doesn't already exist."""
    p = Path(CONFIG.wiki_dir)
    p.mkdir(parents=True, exist_ok=True)
    if not (p / ".git").exists():
        _run(["init", "-b", "main"], cwd=str(p))
        _run(["config", "user.email", "agent-workspace@local"], cwd=str(p))
        _run(["config", "user.name", "agent-workspace"], cwd=str(p))


def commit_file(rel_path: str, body: str, message: str, author: str | None = None) -> str:
    """Write a file at ``rel_path`` (relative to the wiki root), commit, return SHA."""
    full = Path(CONFIG.wiki_dir) / rel_path
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(body)
    _run(["add", rel_path])
    env_args = ["--author", author] if author else []
    _run(["commit", "-m", message, *env_args])
    return _run(["rev-parse", "HEAD"]).stdout.strip()


def read_file(rel_path: str, ref: str = "HEAD") -> str:
    """Read file contents at a given git ref. Default: working tree's last commit."""
    return _run(["show", f"{ref}:{rel_path}"]).stdout


def history(rel_path: str, limit: int = 50) -> list[dict]:
    """Return commit metadata for a path."""
    out = _run(
        ["log", f"-n{limit}", "--pretty=format:%H%x09%an%x09%aI%x09%s", "--", rel_path]
    ).stdout
    rows = []
    for line in out.splitlines():
        sha, author, iso, subject = line.split("\t", 3)
        rows.append({"sha": sha, "author": author, "ts": iso, "message": subject})
    return rows


def list_paths(prefix: str = "") -> list[str]:
    """List tracked files under a path prefix."""
    out = _run(["ls-files", prefix or "."]).stdout
    return [line for line in out.splitlines() if line]


def diff_for_commit(sha: str, rel_path: str | None = None) -> str:
    args = ["show", "--no-color", sha]
    if rel_path:
        args += ["--", rel_path]
    return _run(args).stdout
