"""Path utilities scoped to the wiki working tree."""
from __future__ import annotations

import logging
import os
from pathlib import Path

from app.config import CONFIG
from app.models.wiki import PathMove

log = logging.getLogger(__name__)

# Reserved top-level directory holding trashed items (`.trash/<id>/<path>`).
# It is internal: no user-facing path may live in or resolve into it, so the
# whole feature's isolation hinges on rejecting it here — every read/write/list
# normalizes through safe_rel_path. Trash internals build `.trash/…` paths
# directly (bypassing this guard) via the trash repo. This is the single
# definition; git.py and trash.py import it.
TRASH_DIR = ".trash"
TRASH_PREFIX = TRASH_DIR + "/"

# What makes a tracked file a wiki *page*. A path listing also carries files
# that exist to represent structure rather than content — the `.gitkeep`
# markers that materialize folders (git can't track an empty directory) and
# the `.trigger_*.yaml` files that sit beside the scope they act on. Those are
# neither editable nor visible in the tree, so most callers want pages only.
PAGE_SUFFIX = ".md"


def is_page(rel_path: str) -> bool:
    """Whether ``rel_path`` is a wiki page, as opposed to a folder path or one
    of the structural files that share the tree with pages (see
    ``PAGE_SUFFIX``)."""
    return rel_path.endswith(PAGE_SUFFIX)


def is_trash_path(rel_path: str) -> bool:
    """Whether ``rel_path`` lives in the reserved ``.trash/`` area."""
    normalized = os.path.normpath(rel_path)
    return normalized == TRASH_DIR or normalized.startswith(TRASH_PREFIX)


def safe_rel_path(rel_path: str) -> str:
    """Reject path traversal and the reserved ``.trash/`` area. Returns a
    normalized path or raises ValueError."""
    if rel_path.startswith("/") or ".." in Path(rel_path).parts:
        log.warning("rejected unsafe wiki path: %r", rel_path)
        raise ValueError(f"unsafe path: {rel_path!r}")
    normalized = os.path.normpath(rel_path)
    if is_trash_path(normalized):
        log.warning("rejected access to trash path: %r", rel_path)
        raise ValueError(f"path is in trash and not directly accessible: {rel_path!r}")
    return normalized


def absolute(rel_path: str) -> Path:
    return Path(CONFIG.wiki_dir) / safe_rel_path(rel_path)


def parent_dirs(rel_path: str) -> list[str]:
    """All directories above ``rel_path`` (closest first), used for trigger evaluation.

    The root of the wiki is represented as ``""`` to match the scope_path
    convention used by root-scoped triggers (see ``app/triggers/storage.py``).
    """
    parts = Path(safe_rel_path(rel_path)).parts[:-1]
    out: list[str] = []
    for i in range(len(parts), 0, -1):
        out.append(str(Path(*parts[:i])))
    out.append("")
    return out


def common_folder_rename(
    moves: list[PathMove],
) -> tuple[str | None, str | None]:
    """If every move shares a common ``(old_prefix, new_prefix)`` directory
    swap, return it; else ``(None, None)``.

    ``move_path`` of a directory yields one ``PathMove`` per nested
    file, all sharing the same prefix swap, so this recovers the directory
    rename without the caller having to flag it. Move handlers use it to
    rewrite folder-scoped rows (ACL grants, update policies) in one pass.
    """
    if not moves:
        return None, None
    first_old, first_new = moves[0].old, moves[0].new
    if "/" not in first_old or "/" not in first_new:
        return None, None
    old_prefix = first_old.rsplit("/", 1)[0]
    new_prefix = first_new.rsplit("/", 1)[0]
    while old_prefix and new_prefix:
        suffix_old = first_old[len(old_prefix):]
        suffix_new = first_new[len(new_prefix):]
        if suffix_old != suffix_new:
            return None, None
        if all(
            mv.old.startswith(old_prefix + "/")
            and mv.new.startswith(new_prefix + "/")
            and mv.old[len(old_prefix):] == mv.new[len(new_prefix):]
            for mv in moves
        ):
            return old_prefix, new_prefix
        # Walk up one level and retry — handles nested rename detection.
        if "/" not in old_prefix or "/" not in new_prefix:
            return None, None
        old_prefix = old_prefix.rsplit("/", 1)[0]
        new_prefix = new_prefix.rsplit("/", 1)[0]
    return None, None
