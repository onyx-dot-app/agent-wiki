"""Thin wrapper around the ``git`` CLI for the wiki repo.

The backend keeps the wiki as a real git repository on disk and shells out to
git via subprocess — no library dependency. All writes commit immediately so
history is always consistent with the working tree.
"""

from __future__ import annotations

import logging
import os
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import quote, unquote

from pydantic import BaseModel

from app.config import CONFIG

log = logging.getLogger(__name__)


class CommitInfo(BaseModel):
    """One commit in a path's history (newest first)."""

    sha: str
    author: str
    ts: str  # ISO-8601 author date
    message: str  # commit subject
    body: str  # commit body (may be empty)


def _run(
    args: list[str],
    cwd: str | None = None,
    check: bool = True,
    input: str | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["git", "-c", "core.quotepath=false", *args],
            cwd=cwd or CONFIG.wiki_dir,
            check=check,
            capture_output=True,
            text=True,
            input=input,
            env=env,
        )
    except subprocess.CalledProcessError as e:
        log.error(
            "git %s failed (exit %d): stderr=%r stdout=%r",
            " ".join(args),
            e.returncode,
            (e.stderr or "").strip(),
            (e.stdout or "").strip(),
        )
        raise


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
    listed = _run(["ls-files", "-z", "--", old_rel_path]).stdout.split("\0")
    tracked = [p for p in listed if p]
    moves: list[tuple[str, str]] = []
    for old_p in tracked:
        if old_p == old_rel_path:
            moves.append((old_p, new_rel_path))
        else:
            rest = old_p[len(old_rel_path) :].lstrip("/")
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
    """Remove a tracked file or directory (recursively) and commit. Returns SHA.

    Uses ``-f`` so a working-tree copy that has drifted from HEAD (uncommitted
    local modifications) doesn't block the delete — the user asked to remove
    the path, and the prior contents remain reachable in history.
    """
    _run(["rm", "-rf", "--", rel_path])
    env_args = ["--author", author] if author else []
    _run(["commit", "-m", message, *env_args])
    sha = _run(["rev-parse", "HEAD"]).stdout.strip()
    log.debug("delete_path %s sha=%s author=%s", rel_path, sha[:8], author or "default")
    return sha


def read_file(rel_path: str, ref: str = "HEAD") -> str:
    """Read file contents at a given git ref. Default: working tree's last commit."""
    return _run(["show", f"{ref}:{rel_path}"]).stdout


def path_at_ref(current_rel_path: str, ref: str) -> str | None:
    """Return the path ``current_rel_path`` had at commit ``ref``.

    ``git show <ref>:<path>`` only works if the file was at ``<path>`` in that
    commit — for a renamed file, older commits refer to the old name. Walk
    ``--follow``'s name-status output newest-first, tracking the active name
    across rename boundaries, and report what it was at ``ref``. Returns
    ``None`` if ``ref`` doesn't appear in the file's follow-history.
    """
    out = _run(
        [
            "log",
            "--follow",
            "--name-status",
            "--pretty=format:\x1f%H",
            "--",
            current_rel_path,
        ],
        check=False,
    ).stdout
    sha: str | None = None
    name = current_rel_path
    for line in out.splitlines():
        if line.startswith("\x1f"):
            sha = line[1:]
            continue
        if not line or sha is None:
            continue
        parts = line.split("\t")
        status = parts[0]
        if status.startswith("R") and len(parts) == 3:
            old, new = parts[1], parts[2]
            # The rename commit itself records the file at its new name.
            if sha == ref:
                return new
            # Older commits refer to the pre-rename name.
            if name == new:
                name = old
        elif sha == ref:
            return parts[-1]
    return None


def history(rel_path: str, limit: int = 100) -> list[CommitInfo]:
    """Return commit metadata (incl. body) for a path, newest first."""
    sep_field = "\x1f"
    sep_record = "\x1e"
    fmt = f"%H{sep_field}%an{sep_field}%aI{sep_field}%s{sep_field}%b{sep_record}"
    out = _run(["log", f"-n{limit}", "--follow", f"--pretty=format:{fmt}", "--", rel_path]).stdout
    rows: list[CommitInfo] = []
    for record in out.split(sep_record):
        record = record.strip("\n")
        if not record:
            continue
        parts = record.split(sep_field, 4)
        if len(parts) < 5:
            continue
        sha, author, iso, subject, body = parts
        rows.append(CommitInfo(sha=sha, author=author, ts=iso, message=subject, body=body))
    return rows


def head_sha_for_path(rel_path: str) -> str | None:
    """SHA of the most recent commit that touched ``rel_path``, or None."""
    out = _run(["log", "-n1", "--pretty=format:%H", "--", rel_path], check=False).stdout.strip()
    return out or None


def parent_sha(sha: str) -> str | None:
    """First parent of ``sha`` or None if it's a root commit."""
    out = _run(["rev-parse", "--verify", f"{sha}^"], check=False).stdout.strip()
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
    out = _run(["ls-files", "-z", prefix or "."]).stdout
    return [p for p in out.split("\0") if p]


def paths_touched_since(since_iso: str) -> set[str]:
    """Paths added, modified, renamed, or deleted by any commit at or
    after ``since_iso`` (any timestamp that ``git log --since`` accepts).

    Returns a set — order and frequency don't matter; the caller will
    look up each path's current HEAD sha to decide reindex vs delete.
    Used by the hourly reconcile sweep to scope its work to recent
    activity rather than the whole repo.
    """
    out = _run(
        [
            "log",
            f"--since={since_iso}",
            "--name-only",
            "--diff-filter=AMRD",
            "--pretty=format:",
        ],
        check=False,
    ).stdout
    return {line for line in out.splitlines() if line.strip()}


def list_paths_with_head_sha(prefix: str = "") -> list[tuple[str, str]]:
    """``(path, HEAD-touching sha)`` for every tracked file under ``prefix``.

    One batched ``git log --name-only`` walk newest-first; the first
    sighting of a path wins. Mirrors ``list_paths_with_mtime`` but emits
    ``%H`` instead of ``%aI``. The reconcile sweep uses this to compute
    expected shas for the whole tree in a single subprocess call rather
    than ``head_sha_for_path`` per file.
    """
    sep = "\x1f"
    res = _run(["log", "--name-only", f"--pretty=format:{sep}%H"], check=False)
    if res.returncode != 0:
        return []
    head: dict[str, str] = {}
    current_sha: str | None = None
    for line in res.stdout.splitlines():
        if line.startswith(sep):
            current_sha = line[len(sep) :]
            continue
        if not line or current_sha is None:
            continue
        if line not in head:
            head[line] = current_sha
    tracked = list_paths(prefix)
    return [(p, head.get(p, "")) for p in tracked]


def list_paths_with_mtime(prefix: str = "") -> list[tuple[str, str]]:
    """``(path, ISO-8601 author-time)`` for every tracked file under ``prefix``.

    One batched ``git log`` walk newest-first; the first sighting of a path
    wins. Untracked files (and any that somehow appear in ``ls-files`` but
    not in any commit) get an empty timestamp string.
    """
    sep = "\x1f"
    out = _run(["log", "--name-only", f"--pretty=format:{sep}%aI"]).stdout
    mtime: dict[str, str] = {}
    current_ts: str | None = None
    for line in out.splitlines():
        if line.startswith(sep):
            current_ts = line[len(sep) :]
            continue
        if not line or current_ts is None:
            continue
        if line not in mtime:
            mtime[line] = current_ts
    tracked = list_paths(prefix)
    return [(p, mtime.get(p, "")) for p in tracked]


def paths_changed_in(sha: str) -> list[str]:
    """File paths touched by a single commit. Empty list if sha is unknown."""
    out = _run(["diff-tree", "--no-commit-id", "--name-only", "-r", sha], check=False).stdout
    return [line for line in out.splitlines() if line]


def tree_paths_at(sha: str) -> list[str]:
    """All tracked file paths in the tree at ``sha``."""
    out = _run(["ls-tree", "-r", "--name-only", sha], check=False).stdout
    return [line for line in out.splitlines() if line]


def diff_for_commit(sha: str, rel_path: str | None = None, *, unified: int = 3) -> str:
    args = ["show", "--no-color", f"--unified={unified}", sha]
    if rel_path:
        args += ["--", rel_path]
    return _run(args).stdout


# --------------------------------------------------------------------------- #
# Human drafts — one git branch per (user, page)                             #
# --------------------------------------------------------------------------- #


def _draft_branch(rel_path: str, user_id: str) -> str:
    # Percent-encode the path so spaces and other chars invalid in git ref
    # names are safe. Keep '/' so nested paths stay namespaced naturally.
    return f"drafts/{user_id}/{quote(rel_path, safe='/')}"


def save_draft(rel_path: str, user_id: str, content: str, base_sha: str) -> None:
    """Write ``content`` to the draft branch for ``(rel_path, user_id)``.

    Uses only git plumbing — no working tree checkout. The draft branch always
    has exactly one commit on top of ``base_sha``; each save replaces it so
    ``base_sha`` is always the parent of the branch tip.
    """
    branch = _draft_branch(rel_path, user_id)

    # Store the content as a loose blob object.
    blob_sha = _run(["hash-object", "-w", "--stdin"], input=content).stdout.strip()

    # Build a tree starting from base_sha's tree with rel_path replaced.
    # Use a temp index so we don't disturb the main working-tree index.
    fd, tmp_idx = tempfile.mkstemp(suffix=".idx")
    os.close(fd)
    try:
        idx_env = {**os.environ, "GIT_INDEX_FILE": tmp_idx}
        base_tree = _run(["rev-parse", f"{base_sha}^{{tree}}"]).stdout.strip()
        _run(["read-tree", base_tree], env=idx_env)
        _run(
            ["update-index", "--add", "--cacheinfo", f"100644,{blob_sha},{rel_path}"],
            env=idx_env,
        )
        tree_sha = _run(["write-tree"], env=idx_env).stdout.strip()
    finally:
        Path(tmp_idx).unlink(missing_ok=True)

    commit_sha = _run(
        ["commit-tree", tree_sha, "-p", base_sha, "-m", f"draft: {rel_path}"]
    ).stdout.strip()
    _run(["update-ref", f"refs/heads/{branch}", commit_sha])
    log.debug("save_draft %s user=%s", rel_path, user_id)


def get_draft(rel_path: str, user_id: str) -> dict[str, str] | None:
    """Return ``{path, base_sha, content, updated_at}`` or None if no draft."""
    branch = _draft_branch(rel_path, user_id)
    if _run(["rev-parse", "--verify", f"refs/heads/{branch}"], check=False).returncode != 0:
        return None
    result = _run(["show", f"{branch}:{rel_path}"], check=False)
    if result.returncode != 0:
        return None
    content = result.stdout
    # Branch has exactly one commit on top of base_sha; its parent = base_sha.
    base_sha = _run(["rev-parse", f"{branch}^"]).stdout.strip()
    updated_at = _run(["log", "--format=%aI", "-1", branch]).stdout.strip()
    return {"path": rel_path, "base_sha": base_sha, "content": content, "updated_at": updated_at}


def delete_draft(rel_path: str, user_id: str) -> None:
    """Delete the draft branch for ``(rel_path, user_id)`` if it exists."""
    branch = _draft_branch(rel_path, user_id)
    if _run(["rev-parse", "--verify", f"refs/heads/{branch}"], check=False).returncode != 0:
        return
    _run(["update-ref", "-d", f"refs/heads/{branch}"])
    log.debug("delete_draft %s user=%s", rel_path, user_id)


def delete_drafts_for_path(rel_path: str) -> None:
    """Delete all draft branches for a page — called when the page is deleted."""
    out = _run(["for-each-ref", "--format=%(refname:short)", "refs/heads/drafts/"], check=False).stdout
    for branch in out.splitlines():
        # branch = "drafts/<user_id>/<rel_path>" — split into at most 3 parts
        parts = branch.split("/", 2)
        if len(parts) == 3 and unquote(parts[2]) == rel_path:
            _run(["update-ref", "-d", f"refs/heads/{branch}"], check=False)


class RebaseResult(BaseModel):
    """Result of a draft rebase attempt."""

    merged: str           # merged content (clean) or content with conflict markers
    base_sha: str         # new base SHA (current HEAD)
    clean: bool           # True = no conflicts, False = conflict markers present
    current_body: str     # current HEAD content (for conflict UI)
    draft_body: str       # original draft content (for conflict UI)


class MergeResult(BaseModel):
    """Result of a ``merge_content`` call."""

    merged: str   # merged text (clean) or text with conflict markers
    clean: bool   # True = no conflicts, False = conflict markers present


def merge_content(base_body: str, current_body: str, incoming_body: str) -> MergeResult:
    """3-way merge ``incoming_body`` onto ``current_body`` using ``base_body`` as ancestor.

    Raises ``RuntimeError`` on a hard git error (negative returncode — e.g.
    binary file, permission failure).
    """
    paths: list[str] = []
    try:
        for content in (current_body, base_body, incoming_body):
            fd, p = tempfile.mkstemp(suffix=".txt")
            paths.append(p)
            try:
                os.write(fd, content.encode())
            finally:
                os.close(fd)
        # git merge-file -p writes result to stdout; exit 0 = clean, >0 = conflicts.
        result = _run(
            ["merge-file", "-p", "-L", "current", "-L", "base", "-L", "incoming",
             paths[0], paths[1], paths[2]],
            check=False,
        )
        if result.returncode < 0:
            raise RuntimeError(
                f"git merge-file failed (exit {result.returncode}): {result.stderr.strip()}"
            )
        return MergeResult(merged=result.stdout, clean=result.returncode == 0)
    finally:
        for p in paths:
            Path(p).unlink(missing_ok=True)


def rebase_draft(rel_path: str, user_id: str) -> RebaseResult | None:
    """3-way merge the user's draft onto the current HEAD of ``rel_path``.

    Returns ``None`` if no draft exists or there is no divergence.

    ``clean=True`` means no conflict markers; the caller should call
    ``save_draft`` with the merged content and the new base_sha.
    ``clean=False`` means conflict markers are present; the caller should
    show the conflict panel.
    """
    draft = get_draft(rel_path, user_id)
    if draft is None:
        return None

    head_sha = head_sha_for_path(rel_path)
    if head_sha is None or head_sha == draft["base_sha"]:
        # No divergence — nothing to rebase.
        return None

    current_body = read_file(rel_path)
    base_body = read_file(rel_path, ref=draft["base_sha"])
    draft_body = draft["content"]

    mr = merge_content(base_body, current_body, draft_body)

    return RebaseResult(
        merged=mr.merged,
        base_sha=head_sha,
        clean=mr.clean,
        current_body=current_body,
        draft_body=draft_body,
    )
