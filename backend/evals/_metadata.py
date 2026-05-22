"""Reproducibility metadata captured on every CaseResult."""

from __future__ import annotations

import datetime
import uuid
from pathlib import Path


def new_eval_run_id() -> str:
    return uuid.uuid4().hex[:16]


def utc_iso_now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _resolve_git_dir(start: Path) -> Path | None:
    cur = start if start.is_absolute() else start.resolve()
    while True:
        git_path = cur / ".git"
        if git_path.is_dir():
            return git_path
        if git_path.is_file():
            try:
                text = git_path.read_text().strip()
            except OSError:
                return None
            if text.startswith("gitdir:"):
                rel = text.split(":", 1)[1].strip()
                git_dir = Path(rel)
                if not git_dir.is_absolute():
                    git_dir = (cur / git_dir).resolve()
                return git_dir
        if cur.parent == cur:
            return None
        cur = cur.parent


def _resolve_commondir(git_dir: Path) -> Path:
    """Return the dir holding shared refs for this gitdir (worktree-aware)."""
    commondir_path = git_dir / "commondir"
    if not commondir_path.is_file():
        return git_dir
    try:
        text = commondir_path.read_text().strip()
    except OSError:
        return git_dir
    common = Path(text)
    if not common.is_absolute():
        common = (git_dir / common).resolve()
    return common


def _read_ref(git_dir: Path, commondir: Path, ref: str) -> str:
    for base in (git_dir, commondir):
        ref_path = base / ref
        try:
            return ref_path.read_text().strip()
        except OSError:
            continue
    for base in (git_dir, commondir):
        packed = base / "packed-refs"
        try:
            with packed.open() as fh:
                for line in fh:
                    line = line.strip()
                    if not line or line.startswith("#") or line.startswith("^"):
                        continue
                    parts = line.split(" ", 1)
                    if len(parts) == 2 and parts[1] == ref:
                        return parts[0]
        except OSError:
            continue
    return ""


def git_sha_for(path: Path) -> str:
    """HEAD sha of the repo containing ``path``. Empty string on any error."""
    start = path if path.is_dir() else path.parent
    git_dir = _resolve_git_dir(start)
    if git_dir is None:
        return ""
    commondir = _resolve_commondir(git_dir)
    try:
        head = (git_dir / "HEAD").read_text().strip()
    except OSError:
        return ""
    if head.startswith("ref:"):
        ref = head.split(":", 1)[1].strip()
        return _read_ref(git_dir, commondir, ref)
    return head.strip()
