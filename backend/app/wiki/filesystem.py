"""Path utilities scoped to the wiki working tree."""
from __future__ import annotations

import logging
import os
from pathlib import Path

from app.config import CONFIG

log = logging.getLogger(__name__)


def safe_rel_path(rel_path: str) -> str:
    """Reject path traversal. Returns a normalized path or raises ValueError."""
    if rel_path.startswith("/") or ".." in Path(rel_path).parts:
        log.warning("rejected unsafe wiki path: %r", rel_path)
        raise ValueError(f"unsafe path: {rel_path!r}")
    return os.path.normpath(rel_path)


def absolute(rel_path: str) -> Path:
    return Path(CONFIG.wiki_dir) / safe_rel_path(rel_path)


def parent_dirs(rel_path: str) -> list[str]:
    """All directories above ``rel_path`` (closest first), used for trigger evaluation.

    The root of the wiki is represented as ``""`` to match the scope_path
    convention used by root-scoped triggers (see ``app/triggers/storage.py``).
    """
    parts = Path(safe_rel_path(rel_path)).parts[:-1]
    out = []
    for i in range(len(parts), 0, -1):
        out.append(str(Path(*parts[:i])))
    out.append("")
    return out
