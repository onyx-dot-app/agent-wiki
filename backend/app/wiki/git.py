"""Thin wrapper around the ``git`` CLI for the wiki repo.

The backend keeps the wiki as a real git repository on disk and shells out to
git via subprocess — no library dependency. All writes commit immediately so
history is always consistent with the working tree.
"""

from __future__ import annotations

import fcntl
import logging
import os
import re
import subprocess
import tempfile
import time
from contextlib import contextmanager
from collections.abc import Generator
from datetime import datetime, timedelta, timezone
from pathlib import Path

from pydantic import BaseModel

from app.config import CONFIG
from app.models.wiki import PathMove
from app.wiki import constants
from app.wiki.filesystem import TRASH_DIR, TRASH_PREFIX

log = logging.getLogger(__name__)

_SHA_LINE_RE = re.compile(r"^[0-9a-f]{40}$")

# Trashed items live under `.trash/` (TRASH_PREFIX, defined in filesystem.py).
# Every path enumerator excludes it so trashed content never surfaces in the
# tree, search, recents, or the reconcile sweep — the isolation the Trash
# feature depends on. Trash internals address `.trash/` paths directly.


class CommitInfo(BaseModel):
    """One commit in a path's history (newest first)."""

    sha: str
    author: str
    ts: str  # ISO-8601 author date
    message: str  # commit subject
    body: str  # commit body (may be empty)
    added: int = 0  # lines added to this path by this commit
    removed: int = 0  # lines removed from this path by this commit


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


@contextmanager
def commit_lock() -> Generator[None]:
    """Cross-process exclusive lock serializing the write→add→commit section.

    Every wiki writer (web process, each queue worker, agent tools) operates on
    the same single worktree, so they share one ``.git/index`` and one ref.
    Without serialization a concurrent ``git add`` can overwrite another
    writer's staged blob, so ``git commit`` may commit the wrong content. This
    ``flock`` lives on the shared wiki volume, so it coordinates across every
    process and container mounting it.

    Single-host (docker-compose) semantics only: ``flock`` over a network
    filesystem (NFS) is not reliable, so a multi-host deploy would need a
    Postgres advisory lock or a DB-primary store instead.
    """
    lock_path = Path(CONFIG.wiki_dir) / ".git" / "wiki-commit.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    f = open(lock_path, "a")
    try:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        f.close()  # closing the descriptor releases the flock


def commit_file(
    rel_path: str,
    body: str,
    message: str,
    author: str | None = None,
    *,
    expected_head: str | None = None,
) -> str:
    """Write a file at ``rel_path`` (relative to the wiki root), commit, return SHA.

    The write→add→commit section runs under :func:`commit_lock` so concurrent
    writers can't interleave on the shared index/working tree.

    ``expected_head`` is a compare-and-swap guard for read-modify-write callers:
    when set, the committed body was merged against that SHA, so under the lock
    we re-check HEAD and raise ``GitHeadMovedError`` if a concurrent writer
    advanced it — committing the stale body would clobber the winner. The merge
    loop catches that and re-merges against the new HEAD.

    Retries up to ``_COMMIT_RETRY_MAX`` times on a transient git ref-lock race
    (``cannot lock ref``); raises ``GitCommitLockError`` when that budget is
    exhausted, or ``GitNothingToCommitError`` if the index ends up empty. Both
    are defensive — the lock makes them unreachable from our own writers.
    """
    full = Path(CONFIG.wiki_dir) / rel_path
    full.parent.mkdir(parents=True, exist_ok=True)
    with commit_lock():
        if expected_head is not None:
            current_head = head_sha_for_path(rel_path)
            if current_head != expected_head:
                raise GitHeadMovedError(rel_path, current_head or "")
        full.write_text(body)
        _run(["add", rel_path])
        if _run(["diff", "--cached", "--quiet"], check=False).returncode == 0:
            sha = _run(["rev-parse", "HEAD"]).stdout.strip()
            log.debug("commit_file no-op (no diff) %s sha=%s", rel_path, sha[:8])
            return sha
        env_args = ["--author", author] if author else []
        for attempt in range(_COMMIT_RETRY_MAX + 1):
            try:
                _run(["commit", "-m", message, *env_args])
                break
            except subprocess.CalledProcessError as e:
                out = ((e.stderr or "") + (e.stdout or "")).lower()
                if "cannot lock ref" in out or ("unable to create" in out and ".lock" in out):
                    if attempt >= _COMMIT_RETRY_MAX:
                        raise GitCommitLockError(rel_path) from e
                    log.info(
                        "commit_file: lock race for %s, retrying (%d/%d)",
                        rel_path,
                        attempt + 1,
                        _COMMIT_RETRY_MAX,
                    )
                    time.sleep(0.05 * (attempt + 1))
                    continue
                if "nothing to commit" in out or "nothing added to commit" in out:
                    raise GitNothingToCommitError(rel_path) from e
                raise
        sha = _run(["rev-parse", "HEAD"]).stdout.strip()
    log.debug("commit_file %s sha=%s author=%s", rel_path, sha[:8], author or "default")
    return sha


# Default retry budget for the commit retry helper below. Small: the
# ref-lock race resolves in milliseconds, so a couple of retries is plenty.
_COMMIT_RETRY_MAX = 3


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
    env_args = ["--author", author] if author else []
    with commit_lock():
        full_new.write_text(body)
        _run(["add", new_rel_path])
        _run(["rm", "--", old_rel_path])
        _run(["commit", "-m", message, *env_args])
        sha = _run(["rev-parse", "HEAD"]).stdout.strip()
    log.debug("move_and_commit %s -> %s sha=%s", old_rel_path, new_rel_path, sha[:8])
    return sha


def move_path(
    old_rel_path: str,
    new_rel_path: str,
    message: str,
    author: str | None = None,
) -> tuple[str, list[PathMove]]:
    """Rename a tracked file or directory via ``git mv``, single commit.

    Returns ``(sha, moves)`` where each ``PathMove`` is one tracked file
    that was actually moved. For a directory rename this lists every
    nested file. Used by tools that move things without rewriting content.
    """
    listed = _run(["ls-files", "-z", "--", old_rel_path]).stdout.split("\0")
    tracked = [p for p in listed if p]
    moves: list[PathMove] = []
    for old_p in tracked:
        if old_p == old_rel_path:
            moves.append(PathMove(old=old_p, new=new_rel_path))
        else:
            rest = old_p[len(old_rel_path) :].lstrip("/")
            moves.append(PathMove(old=old_p, new=f"{new_rel_path}/{rest}"))
    full_new = Path(CONFIG.wiki_dir) / new_rel_path
    full_new.parent.mkdir(parents=True, exist_ok=True)
    env_args = ["--author", author] if author else []
    with commit_lock():
        _run(["mv", old_rel_path, new_rel_path])
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
    env_args = ["--author", author] if author else []
    with commit_lock():
        _run(["rm", "-rf", "--", rel_path])
        _run(["commit", "-m", message, *env_args])
        sha = _run(["rev-parse", "HEAD"]).stdout.strip()
    log.debug("delete_path %s sha=%s author=%s", rel_path, sha[:8], author or "default")
    return sha


def read_file(rel_path: str, ref: str = "HEAD") -> str:
    """Read file contents at a given git ref. Default: working tree's last commit.

    Raises ``UnknownSha`` when ``ref`` (or the path at that ref) can't be
    resolved, so callers get a typed error instead of a leaked
    ``subprocess.CalledProcessError``.
    """
    try:
        return _run(["show", f"{ref}:{rel_path}"]).stdout
    except subprocess.CalledProcessError as e:
        raise UnknownSha(ref) from e


def read_file_opt(rel_path: str, ref: str = "HEAD") -> str | None:
    """Like ``read_file`` but returns ``None`` when the path doesn't exist
    at ``ref`` — and stays quiet about it.

    A path absent at a ref is a normal, expected outcome for time-windowed
    diffs (a file that's brand-new in the window has no body at the window
    start), so this skips the ERROR log ``read_file`` would emit.
    """
    res = _run(["show", f"{ref}:{rel_path}"], check=False)
    return res.stdout if res.returncode == 0 else None


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
    """Return commit metadata (incl. body + per-commit line stats) for a
    path, newest first."""
    sep_field = "\x1f"
    sep_record = "\x1e"
    fmt = f"%H{sep_field}%an{sep_field}%aI{sep_field}%s{sep_field}%b{sep_record}"
    out = _run(["log", f"-n{limit}", "--follow", f"--pretty=format:{fmt}", "--", rel_path]).stdout
    stats = _numstat_by_sha(rel_path, limit)
    rows: list[CommitInfo] = []
    for record in out.split(sep_record):
        record = record.strip("\n")
        if not record:
            continue
        parts = record.split(sep_field, 4)
        if len(parts) < 5:
            continue
        sha, author, iso, subject, body = parts
        added, removed = stats.get(sha, (0, 0))
        rows.append(
            CommitInfo(
                sha=sha,
                author=author,
                ts=iso,
                message=subject,
                body=body,
                added=added,
                removed=removed,
            )
        )
    return rows


def _numstat_by_sha(rel_path: str, limit: int) -> dict[str, tuple[int, int]]:
    """``{sha: (added, removed)}`` for a path's history in one git call.

    ``git log --numstat`` emits a bare sha line per commit followed by
    ``<added>\\t<removed>\\t<path>`` rows. Binary files report ``-`` for
    the counts, which we coerce to 0. ``--follow`` may render a rename as
    ``old => new`` in the path column; the counts column is unaffected.
    """
    out = _run(
        ["log", f"-n{limit}", "--follow", "--numstat", "--format=%H", "--", rel_path],
        check=False,
    ).stdout
    stats: dict[str, tuple[int, int]] = {}
    current: str | None = None
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        if _SHA_LINE_RE.match(line):
            current = line
            continue
        if current is None:
            continue
        cols = line.split("\t")
        if len(cols) < 2:
            continue
        added = int(cols[0]) if cols[0].isdigit() else 0
        removed = int(cols[1]) if cols[1].isdigit() else 0
        prev = stats.get(current, (0, 0))
        stats[current] = (prev[0] + added, prev[1] + removed)
    return stats


def head_sha() -> str | None:
    """SHA of the repo HEAD, or None on an empty repo."""
    out = _run(["rev-parse", "--verify", "HEAD"], check=False).stdout.strip()
    return out or None


def changed_paths_between(base_sha: str, tip_sha: str) -> list[str]:
    """Paths touched by any commit in ``base_sha..tip_sha`` (name-only diff of
    the endpoints — renames/moves report both sides)."""
    out = _run(["diff", "--name-only", base_sha, tip_sha]).stdout
    return [p for p in out.splitlines() if p.strip()]


def revert_to(base_sha: str, message: str, author: str | None = None) -> str | None:
    """Additively restore the working tree to ``base_sha``'s state in one new
    commit — the history-preserving undo for everything after ``base_sha``.

    Not ``reset --hard`` (history must stay additive): the commits being
    undone remain in history; a new commit lands whose tree equals
    ``base_sha``'s. Returns the revert commit's SHA, or ``None`` when HEAD is
    already at ``base_sha`` (nothing to revert).
    """
    env_args = ["--author", author] if author else []
    with commit_lock():
        head = _run(["rev-parse", "--verify", "HEAD"]).stdout.strip()
        if head == base_sha:
            return None
        # checkout <base> -- . restores tracked content; deletions since base
        # need the index reset too, so restore both index and worktree.
        _run(["checkout", base_sha, "--", "."])
        # Files created after base_sha aren't touched by checkout — remove
        # anything tracked now that didn't exist at base.
        base_files = set(
            _run(["ls-tree", "-r", "--name-only", base_sha]).stdout.splitlines()
        )
        now_files = set(_run(["ls-files"]).stdout.splitlines())
        for extra in sorted(now_files - base_files):
            _run(["rm", "-f", "--", extra])
        _run(["commit", "-m", message, *env_args])
        sha = _run(["rev-parse", "HEAD"]).stdout.strip()
    log.info("revert_to %s sha=%s", base_sha[:8], sha[:8])
    return sha


def head_sha_for_path(rel_path: str) -> str | None:
    """SHA of the most recent commit that touched ``rel_path``, or None."""
    out = _run(["log", "-n1", "--pretty=format:%H", "--", rel_path], check=False).stdout.strip()
    return out or None


def parent_sha(sha: str) -> str | None:
    """First parent of ``sha`` or None if it's a root commit."""
    out = _run(["rev-parse", "--verify", f"{sha}^"], check=False).stdout.strip()
    return out or None


def is_ancestor(ancestor: str, descendant: str) -> bool:
    """True if ``ancestor`` is an ancestor of ``descendant`` (or the same commit).

    ``git merge-base --is-ancestor`` exits 0 when it is, 1 when not; any other
    exit (bad SHA, etc.) is treated as "not an ancestor"."""
    return _run(["merge-base", "--is-ancestor", ancestor, descendant], check=False).returncode == 0


def commits_between(base_sha: str, head_sha: str, rel_path: str) -> list[str]:
    """SHAs reachable from head_sha but not base_sha that touched rel_path,
    newest first. Excludes base_sha itself."""
    out = _run(
        ["log", "--pretty=format:%H", f"{base_sha}..{head_sha}", "--", rel_path],
        check=False,
    ).stdout
    return [s for s in out.splitlines() if s]


def count_commits_since(rel_path: str, *, author: str, since_iso: str) -> int:
    """Number of commits since ``since_iso`` by ``author`` that touched
    ``rel_path``.

    ``rel_path`` may be a single ``.md`` file or a folder — git's pathspec
    counts every commit touching anything beneath a folder, so a folder count
    aggregates its pages. Empty path scopes the whole repo. ``author`` is a
    bare email; we anchor it as ``<email>$`` so git's ``--author`` regex matches
    that exact identity rather than substring-matching it (e.g. so
    ``onyx-ingest@local`` can't match ``onyx-ingest@localhost``). ``since_iso``
    is any timestamp git ``--since`` accepts (filters by commit date)."""
    author_pattern = f"<{author}>$"
    out = _run(
        [
            "log",
            f"--since={since_iso}",
            f"--author={author_pattern}",
            "--pretty=format:%H",
            "--",
            rel_path or ".",
        ],
        check=False,
    ).stdout
    return sum(1 for line in out.splitlines() if line.strip())


_INGEST_WINDOW_HOURS = 24


def ingest_update_times_24h(rel_path: str) -> list[int]:
    """Committer unix timestamps of ``Onyx Ingest`` auto-update commits to
    ``rel_path`` in the trailing 24h, oldest first.

    ``len(...)`` is the rolling update count; the timestamps let callers work
    out when an over-cap page drops back under the cap (the oldest updates age
    out of the 24h window)."""
    since = (
        datetime.now(timezone.utc) - timedelta(hours=_INGEST_WINDOW_HOURS)
    ).isoformat()
    out = _run(
        [
            "log",
            f"--since={since}",
            f"--author=<{constants.INGEST_AUTHOR_EMAIL}>$",
            "--pretty=format:%ct",
            "--",
            rel_path or ".",
        ],
        check=False,
    ).stdout
    return sorted(int(line) for line in out.splitlines() if line.strip())


def list_paths(prefix: str = "") -> list[str]:
    """List tracked files under a path prefix (excluding the hidden ``.trash/``)."""
    out = _run(["ls-files", "-z", prefix or "."]).stdout
    return [p for p in out.split("\0") if p and not p.startswith(TRASH_PREFIX)]


def bundle(dest_path: str) -> None:
    """Write a ``git bundle`` of the whole repo (all refs + full history)
    to ``dest_path``.

    A bundle is a single portable file that ``git clone`` accepts directly,
    which makes it the backup format for the wiki: restoring is
    ``git clone <bundle> <dir>`` with nothing lost. Read-only on the repo —
    safe to run against a live working tree without the commit lock.
    """
    _run(["bundle", "create", dest_path, "--all"])


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
    return {
        line
        for line in out.splitlines()
        if line.strip() and not line.startswith(TRASH_PREFIX)
    }


def rev_before(iso: str) -> str | None:
    """The newest commit at or before ``iso`` (any timestamp ``git rev-list
    --before`` accepts), or ``None`` if the repo has no commit that old.

    Used to resolve the "before" ref for a time-windowed diff: diffing this
    ref against HEAD captures the net change over ``[iso, now]``.
    """
    out = _run(
        ["rev-list", "-1", f"--before={iso}", "HEAD"],
        check=False,
    ).stdout.strip()
    return out or None


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


def paths_authored_by(author_email: str, limit: int = 50) -> list[tuple[str, str]]:
    """``(.md path, ISO author-time)`` for files this author last touched.

    Newest-first by author-time, one entry per path (first sighting wins).
    Used by the home "Recent Pages" grid to surface pages the current user
    has actually worked on (created or edited), not every recent page.
    """
    sep = "\x1f"
    # Bound the walk so a deep history isn't fully scanned on every home-page
    # load. A path can recur across commits, so allow generous headroom over
    # `limit` to still collect that many distinct paths.
    out = _run(
        [
            "log",
            "--max-count=%d" % (limit * 20),
            # --author is a regex; escape so metachars in an email (".", "+")
            # match literally and can't pull in or skip the wrong author.
            "--author=%s" % re.escape(author_email),
            "--name-only",
            "--pretty=format:%s%%aI" % sep,
        ],
        check=False,
    ).stdout
    seen: dict[str, str] = {}
    current_ts: str | None = None
    for line in out.splitlines():
        if line.startswith(sep):
            current_ts = line[len(sep) :]
            continue
        if not line or current_ts is None:
            continue
        if (
            line.endswith(".md")
            and not line.startswith(TRASH_PREFIX)
            and line not in seen
        ):
            seen[line] = current_ts
    return list(seen.items())[:limit]


def paths_changed_in(sha: str) -> list[str]:
    """File paths touched by a single commit. Empty list if sha is unknown."""
    out = _run(["diff-tree", "--no-commit-id", "--name-only", "-r", sha], check=False).stdout
    return [line for line in out.splitlines() if line]


def tree_paths_at(sha: str) -> list[str]:
    """All tracked file paths in the tree at ``sha``."""
    out = _run(["ls-tree", "-r", "--name-only", sha], check=False).stdout
    return [line for line in out.splitlines() if line]


def list_trash_files() -> list[str]:
    """Tracked files currently under ``.trash/`` — the raw trash contents.

    Deliberately bypasses the ``.trash/`` exclusion the other enumerators apply;
    only the trash repo (``app/wiki/trash.py``) should call it.
    """
    out = _run(["ls-files", "-z", "--", TRASH_DIR]).stdout
    return [p for p in out.split("\0") if p]


def trash_ids_newest_first() -> list[str]:
    """Trash ids ordered by their trash-move commit, newest first.

    Deterministic even when two moves land in the same author-date second —
    sorting entries by the ``trashed_at`` *string* can't disambiguate those, but
    git's commit history can. Walks additions under ``.trash/`` newest-commit
    first and records each id at its first (i.e. creating) appearance.
    """
    out = _run(
        ["log", "--diff-filter=A", "--name-only", "--format=", "--", TRASH_DIR],
        check=False,
    ).stdout
    order: list[str] = []
    seen: set[str] = set()
    for line in out.splitlines():
        line = line.strip()
        if not line.startswith(TRASH_PREFIX):
            continue
        tid = line[len(TRASH_PREFIX) :].split("/", 1)[0]
        if tid and tid not in seen:
            seen.add(tid)
            order.append(tid)
    return order


def last_commit_meta_for_path(rel_path: str) -> tuple[str, str, str, str] | None:
    """``(sha, author, ISO-ts, message)`` of the most recent commit touching
    ``rel_path``, or ``None``. For a trashed path this is the trash-move commit —
    who/when plus the full message, whose ``Trash-Original`` trailer records the
    root that was trashed (see ``app/wiki/trash.py``). ``message`` is placed last
    so its embedded newlines can't be confused with a field separator."""
    sep = "\x1f"
    out = _run(
        ["log", "-n1", f"--pretty=format:%H{sep}%an{sep}%aI{sep}%B", "--", rel_path],
        check=False,
    ).stdout.strip()
    if not out:
        return None
    parts = out.split(sep, 3)
    return (parts[0], parts[1], parts[2], parts[3]) if len(parts) == 4 else None


def _prune_empty_trash_dirs(trash_id: str) -> None:
    """Remove now-empty working-tree dirs under ``.trash/<trash_id>/`` left by a
    restore (``git mv`` out) or purge (``git rm``). Git doesn't track empty
    directories, so this is a filesystem-only cleanup — nothing to commit."""
    root = Path(CONFIG.wiki_dir) / TRASH_DIR / trash_id
    if not root.exists():
        return
    # Deepest-first so a dir is emptied before we try to remove it.
    for d in sorted(
        (p for p in root.rglob("*") if p.is_dir()),
        key=lambda p: len(p.parts),
        reverse=True,
    ):
        try:
            d.rmdir()
        except OSError:
            pass  # still holds something (unexpected) — leave it
    try:
        root.rmdir()
    except OSError:
        pass


def restore_from_trash(
    trash_id: str, message: str, author: str | None = None
) -> tuple[str, list[PathMove]]:
    """Move every file under ``.trash/<trash_id>/`` back to its original path
    (the path with the ``.trash/<trash_id>/`` prefix stripped), one commit.

    File-granular so restoring a single page doesn't collide with a still-live
    sibling in the same folder. Returns ``(sha, moves)`` for the caller to
    re-point path-keyed metadata via ``after_path_move``. Raises
    ``GitNothingToCommitError`` if the trash id has no files.
    """
    prefix = f"{TRASH_PREFIX}{trash_id}/"
    trashed = [p for p in list_trash_files() if p.startswith(prefix)]
    if not trashed:
        raise GitNothingToCommitError(prefix)
    moves = [PathMove(old=p, new=p[len(prefix) :]) for p in trashed]
    env_args = ["--author", author] if author else []
    with commit_lock():
        for mv in moves:
            (Path(CONFIG.wiki_dir) / mv.new).parent.mkdir(parents=True, exist_ok=True)
            _run(["mv", mv.old, mv.new])
        _run(["commit", "-m", message, *env_args])
        sha = _run(["rev-parse", "HEAD"]).stdout.strip()
    _prune_empty_trash_dirs(trash_id)
    log.info("restore_from_trash %s (%d files) sha=%s", trash_id, len(moves), sha[:8])
    return sha, moves


def purge_from_trash(
    trash_id: str, message: str, author: str | None = None
) -> str | None:
    """Permanently remove ``.trash/<trash_id>/`` from the working tree via
    ``git rm`` and commit. **Soft purge**: the content stays in git *history*
    (we never rewrite history — additive-only), it just leaves the working tree
    and therefore the Trash view. Returns the commit sha, or ``None`` when the
    trash id has no tracked files (already purged/restored)."""
    prefix = f"{TRASH_PREFIX}{trash_id}/"
    files = [p for p in list_trash_files() if p.startswith(prefix)]
    if not files:
        return None
    env_args = ["--author", author] if author else []
    with commit_lock():
        _run(["rm", "-r", "--", f"{TRASH_DIR}/{trash_id}"])
        _run(["commit", "-m", message, *env_args])
        sha = _run(["rev-parse", "HEAD"]).stdout.strip()
    _prune_empty_trash_dirs(trash_id)
    log.info("purge_from_trash %s (%d files) sha=%s", trash_id, len(files), sha[:8])
    return sha


class UnknownSha(Exception):
    """Raised when a SHA can't be resolved against the wiki repo.

    Lets callers translate "this commit/ref doesn't exist" without leaking
    ``subprocess.CalledProcessError`` outside the git seam.
    """


class GitCommitLockError(Exception):
    """Raised when ``git commit`` can't acquire git's own ref/index lock.

    Defensive backstop, not an expected outcome of our concurrency. Our own
    writers all serialize through :func:`commit_lock`, so two of *our* commits
    can never race for the ref lock. The only way to hit this is a process that
    mutates the repo *without* taking that flock:

    - git's background auto-gc / maintenance
    - a manual ``git`` command, backup, or fsck on the ``wiki-data`` volume
    - a future code path that bypasses ``commit_lock``

    ``commit_file`` retries transparently (``_COMMIT_RETRY_MAX`` attempts with
    backoff) to absorb such a transient out-of-band lock; this error is the
    terminal "retries exhausted" signal. It is currently uncaught and surfaces
    as a 5xx — seeing one in logs means something is writing to the repo
    outside the lock, which is the bug to chase rather than the commit path.
    """


class GitNothingToCommitError(Exception):
    """Raised when ``git commit`` finds nothing staged.

    Like :class:`GitCommitLockError`, this is now defensive: our own
    ``add``→``commit`` runs inside :func:`commit_lock`, so a concurrent writer
    of ours can't reset the index between the two steps. It remains reachable
    only if something mutates the index out-of-band (see the bypass cases on
    ``GitCommitLockError``). The merge loop treats it as a retry trigger
    (re-read HEAD, re-merge) rather than leaking a raw ``CalledProcessError``.
    """


class GitHeadMovedError(Exception):
    """Raised by ``commit_file`` when HEAD no longer matches ``expected_head``.

    Compare-and-swap guard: a read-modify-write caller merged its body against
    ``expected_head``; if a concurrent writer advanced HEAD before we took the
    commit lock, committing the stale body would clobber the winner. The merge
    loop catches this and re-merges against the new HEAD.
    """

    def __init__(self, rel_path: str, current_sha: str) -> None:
        super().__init__(f"HEAD moved for {rel_path}: now {current_sha[:8] or '<none>'}")
        self.rel_path = rel_path
        self.current_sha = current_sha


class GitMergeConflictError(Exception):
    """Raised by ``commit_and_fan_out`` when a concurrent change can't be
    merged cleanly and ``ai_merge`` is not set. Human edit paths translate
    this into a 409 so the user gets the conflict UI.
    """

    def __init__(self, rel_path: str) -> None:
        self.rel_path = rel_path
        super().__init__(f"merge conflict on {rel_path}")


def diff_for_commit(sha: str, rel_path: str | None = None, *, unified: int = 3) -> str:
    args = ["show", "--no-color", f"--unified={unified}", sha]
    if rel_path:
        args += ["--", rel_path]
    try:
        return _run(args).stdout
    except subprocess.CalledProcessError as e:
        raise UnknownSha(sha) from e


# --------------------------------------------------------------------------- #
# Human drafts — one git branch per (user, page)                             #
# --------------------------------------------------------------------------- #


class MergeResult(BaseModel):
    """Result of a ``merge_content`` call."""

    merged: str  # merged text (clean) or text with conflict markers
    clean: bool  # True = no conflicts, False = conflict markers present


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
            [
                "merge-file",
                "-p",
                "-L",
                "current",
                "-L",
                "base",
                "-L",
                "incoming",
                paths[0],
                paths[1],
                paths[2],
            ],
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
