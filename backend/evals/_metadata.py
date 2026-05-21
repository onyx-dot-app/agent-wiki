"""Reproducibility metadata captured on every CaseResult."""

from __future__ import annotations

import datetime
import subprocess
import uuid
from pathlib import Path


def new_eval_run_id() -> str:
    return uuid.uuid4().hex[:16]


def utc_iso_now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def git_sha_for(path: Path) -> str:
    """HEAD sha of the repo containing ``path``. Empty string on any error
    (out-of-repo, git not available)."""
    try:
        out = subprocess.run(
            ["git", "-C", str(path.parent if path.is_file() else path), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
        )
        return out.stdout.strip() if out.returncode == 0 else ""
    except Exception:
        return ""
