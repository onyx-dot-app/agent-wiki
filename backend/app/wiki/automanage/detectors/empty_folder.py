"""Empty-folder detector — the first, mechanical technique (no LLM, ~free).

Proposes ``delete_empty_folder`` for a folder that holds no pages and has been
empty long enough that it isn't just a folder someone made moments ago and is
about to fill.

A wiki folder is a directory materialized by a committed ``.gitkeep`` marker
(git can't track empty directories — see ``create_folder`` in
``app/api/wiki.py``). "Empty" here means the folder's entire subtree contains
*only* ``.gitkeep`` markers: no ``.md`` page, and nothing else either (a
lingering folder-scoped ``.trigger_*.yaml`` keeps the folder alive, so it is
deliberately *not* treated as empty). "Empty since" is the last commit that
touched anything under the folder — the removal/move that emptied it, or its
creation — read from ``git.last_commit_meta_for_path``, which accepts a folder
prefix. A folder younger than ``min_age_days`` is left alone.

For nested empties we emit one proposal for the **maximal** empty folder (the
shallowest whose parent is not itself empty); deleting it cascades the empty
descendants, so N redundant proposals collapse to one. The executor removes
directories deepest-first within that folder.
"""
from __future__ import annotations

import logging
from collections.abc import Sequence
from typing import Any
from datetime import datetime, timedelta, timezone

from app.wiki import git
from app.wiki.automanage.detectors.base import (
    ProposalDraft,
    Scope,
    TriggerKind,
)
from app.wiki.change_proposals import ProposalOp

log = logging.getLogger(__name__)

# Detector-specific config lives on the detector, not the protocol. The grace
# window before a folder that went empty is worth proposing for deletion.
EMPTY_FOLDER_MIN_AGE_DAYS = 2

_GITKEEP = ".gitkeep"


def _ancestor_dirs(path: str) -> list[str]:
    """Every directory prefix of a tracked file path, excluding the file
    itself and the repo root (``""``). ``a/b/c.md`` → ``["a", "a/b"]``."""
    parts = path.split("/")
    return ["/".join(parts[:i]) for i in range(1, len(parts))]


def _parent_dir(folder: str) -> str:
    """The parent folder, or ``""`` (root) for a top-level folder."""
    return folder.rsplit("/", 1)[0] if "/" in folder else ""


def _maximal_empty_folders(paths: Sequence[str]) -> list[str]:
    """The maximal empty folders among ``paths``, sorted for determinism.

    Pure over the tracked-file list. A folder is empty when every tracked file
    beneath it is a ``.gitkeep``; it is *maximal* when its parent isn't also
    empty (so a proposal on the parent doesn't already subsume it)."""
    all_folders: set[str] = set()
    non_empty: set[str] = set()
    for p in paths:
        dirs = _ancestor_dirs(p)
        all_folders.update(dirs)
        if p.rsplit("/", 1)[-1] != _GITKEEP:
            # A real file makes every folder above it non-empty.
            non_empty.update(dirs)
    empty = all_folders - non_empty
    return sorted(f for f in empty if _parent_dir(f) not in empty)


def _empty_long_enough(empty_since_iso: str, now: datetime, min_age_days: int) -> bool:
    """Pure age gate. ``empty_since_iso`` is a strict-ISO commit timestamp
    (git ``%aI``). A malformed value fails closed (not stale)."""
    try:
        since = datetime.fromisoformat(empty_since_iso)
    except ValueError:
        log.warning("empty-folder: unparseable commit ts %r", empty_since_iso)
        return False
    return now - since >= timedelta(days=min_age_days)


class _EmptyFolderDetector:
    name = "empty_folder"
    pairs_paths = False  # single-path op; sees the whole scope

    def __init__(self, *, min_age_days: int = EMPTY_FOLDER_MIN_AGE_DAYS) -> None:
        self.min_age_days = min_age_days

    def applicable(self, trigger: TriggerKind) -> bool:
        # A page *create* can never empty a folder; a sweep or a write (which
        # may be a delete/move that empties one) can.
        return trigger in (TriggerKind.SWEEP, TriggerKind.ON_WRITE)

    def detect(self, scope: Scope) -> list[ProposalDraft]:
        if not self.applicable(scope.trigger):
            return []
        now = datetime.now(timezone.utc)
        drafts: list[ProposalDraft] = []
        for folder in _maximal_empty_folders(scope.paths):
            meta = git.last_commit_meta_for_path(folder)
            if meta is None:
                continue
            _sha, _author, ts_iso, _msg = meta
            if not _empty_long_enough(ts_iso, now, self.min_age_days):
                continue
            drafts.append(
                ProposalDraft(
                    op=ProposalOp.DELETE_EMPTY_FOLDER,
                    source_paths=[folder],
                    summary=f"Delete empty folder “{folder}”",
                )
            )
        return drafts

    def validate(self, proposal: dict[str, Any]) -> str | None:
        """Premise: the folder is still empty (only ``.gitkeep`` markers)."""
        folder = proposal["source_paths"][0]
        under = list(git.list_paths(folder))
        still_empty = bool(under) and all(
            p.rsplit("/", 1)[-1] == _GITKEEP for p in under
        )
        if not still_empty:
            return f"{folder!r} is no longer an empty folder"
        return None


DETECTOR = _EmptyFolderDetector()
