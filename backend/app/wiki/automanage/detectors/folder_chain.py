"""Single-child folder-chain flattening detector — mechanical, no LLM.

A **chain** is a run of folders that each contain exactly one thing: the next
folder (a ``.gitkeep`` marker alongside it doesn't count). The chain's
**tail** is the first folder with real content — pages, or branching
subfolders. Every level between the head and the tail is a pass-through that
adds a click and a path segment but no organization; the proposal flattens
them by moving the tail's pages up into the chain head, preserving the
structure *below* the tail (``head/link/tail/sub/x.md`` → ``head/sub/x.md``).

Division of labor with the other mechanics:

- The applier only **moves pages** (never edits bodies — the executor's
  move-must-preserve-content rail holds it to that). The emptied wrapper
  folders keep their ``.gitkeep`` markers and are left behind on purpose:
  the empty-folder detector proposes their deletion on a later pass, after
  its own grace window. Detectors compose through wiki state, not through
  each other.
- An entirely empty chain (no pages anywhere) is the empty-folder detector's
  case, not this one's — the tail here must hold at least one page.

``source_paths`` carries the chain folders themselves (head first, tail
last) ahead of the page paths: the folders are the subject of the operation,
they widen the applier's path scope to the wrappers, and they let
``validate`` re-derive the chain without guessing it back out of the page
paths. ``target_paths`` is the destination page paths, index-aligned with
the page sources.

Never auto-approvable: which folder name survives a flatten is naming
judgment (the head's name wins here), so a human confirms it even in an
AI-managed scope.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any

from app.wiki import git
from app.wiki.automanage.detectors.base import ProposalDraft, Scope, TriggerKind
from app.wiki.change_proposals import ProposalOp

log = logging.getLogger(__name__)

# Grace window on the chain head's last activity: a structure someone is
# actively building (this month's folder just went in; pages are landing)
# shouldn't be proposed for flattening the moment it looks redundant.
FOLDER_CHAIN_MIN_AGE_DAYS = 7

_GITKEEP = ".gitkeep"


class _Tree:
    """Direct-children view of a tracked-file list, for chain walking."""

    def __init__(self, paths: list[str]) -> None:
        self.subdirs: dict[str, set[str]] = defaultdict(set)
        self.plain_files: dict[str, set[str]] = defaultdict(set)
        for p in paths:
            parts = p.split("/")
            for i in range(1, len(parts)):
                self.subdirs["/".join(parts[: i - 1])].add("/".join(parts[:i]))
            if parts[-1] != _GITKEEP:
                self.plain_files["/".join(parts[:-1])].add(p)

    def is_link(self, folder: str) -> bool:
        """A pass-through level: exactly one subfolder, no direct files
        besides a ``.gitkeep``. The root (``""``) is never a link — there is
        no level above it to flatten into."""
        return (
            folder != ""
            and len(self.subdirs.get(folder, ())) == 1
            and not self.plain_files.get(folder)
        )

    def walk_chain(self, head: str) -> str | None:
        """Follow single-child links from ``head`` down to the tail — the
        first descendant that isn't a link. None if ``head`` isn't a link."""
        if not self.is_link(head):
            return None
        cur = head
        while self.is_link(cur):
            (cur,) = self.subdirs[cur]
        return cur


def _parent_dir(folder: str) -> str:
    return folder.rsplit("/", 1)[0] if "/" in folder else ""


def _chain_dirs(head: str, tail: str) -> list[str]:
    """The chain's folder levels, head first, tail last:
    ``("a", "a/b/c")`` → ``["a", "a/b", "a/b/c"]``."""
    parts = tail.split("/")
    depth = len(head.split("/"))
    return ["/".join(parts[:i]) for i in range(depth, len(parts) + 1)]


def _old_enough(head: str, now: datetime, min_age_days: int) -> bool:
    """Last activity anywhere under ``head`` is older than the grace window.
    Missing/unparseable history fails closed (not old enough)."""
    meta = git.last_commit_meta_for_path(head)
    if meta is None:
        return False
    try:
        ts = datetime.fromisoformat(meta[2])
    except ValueError:
        log.warning("folder-chain: unparseable commit ts %r for %r", meta[2], head)
        return False
    return now - ts >= timedelta(days=min_age_days)


def _plan_moves(
    head: str, tail: str, tree: _Tree, taken_lower: set[str]
) -> tuple[list[str], list[str]] | None:
    """The concrete page moves for flattening ``tail`` into ``head``, or None
    when the chain isn't safely flattenable: non-page content under the tail
    (folder-scoped triggers, attachments — moving those is out of scope), or
    a destination that isn't free (judged case-insensitively so a flatten
    can't manufacture a case collision — e.g. a tail subfolder named like a
    chain link)."""
    pages: list[str] = []
    stack = [tail]
    while stack:
        d = stack.pop()
        for f in sorted(tree.plain_files.get(d, ())):
            if not f.endswith(".md"):
                return None
            pages.append(f)
        stack.extend(tree.subdirs.get(d, ()))
    if not pages:
        return None  # empty chain — the empty-folder detector's case
    pages.sort()
    dests = [f"{head}/{p[len(tail) + 1 :]}" for p in pages]
    if any(d.lower() in taken_lower for d in dests):
        return None
    return pages, dests


class _FolderChainDetector:
    name = "folder_chain"
    pairs_paths = False  # subtree op; sees the whole scope

    def __init__(self, *, min_age_days: int = FOLDER_CHAIN_MIN_AGE_DAYS) -> None:
        self.min_age_days = min_age_days

    def applicable(self, trigger: TriggerKind) -> bool:
        # A delete or move can leave a folder single-child; a page create
        # never removes a sibling, so only sweeps and writes can surface one.
        return trigger in (TriggerKind.SWEEP, TriggerKind.ON_WRITE)

    def detect(self, scope: Scope) -> list[ProposalDraft]:
        if not self.applicable(scope.trigger):
            return []
        tree = _Tree(list(scope.paths))
        taken_lower = {p.lower() for p in scope.paths}
        now = datetime.now(UTC)
        drafts: list[ProposalDraft] = []
        accepted_tails: list[str] = []
        # Maximal heads (parent isn't itself a link), shallowest first; a
        # chain nested inside an accepted chain's tail is skipped — the outer
        # flatten recreates it under the head, and a later pass re-detects it.
        heads = sorted(
            (f for f in tree.subdirs if tree.is_link(f) and not tree.is_link(_parent_dir(f))),
            key=lambda f: (f.count("/"), f),
        )
        for head in heads:
            if any(head.startswith(t + "/") for t in accepted_tails):
                continue
            tail = tree.walk_chain(head)
            if tail is None:
                continue
            plan = _plan_moves(head, tail, tree, taken_lower)
            if plan is None:
                continue
            if not _old_enough(head, now, self.min_age_days):
                continue
            pages, dests = plan
            accepted_tails.append(tail)
            moves = "\n".join(f"- “{s}” → “{d}”" for s, d in zip(pages, dests))
            drafts.append(
                ProposalDraft(
                    op=ProposalOp.MOVE,
                    source_paths=_chain_dirs(head, tail) + pages,
                    target_paths=dests,
                    summary=(
                        f"Flatten single-child folder chain “{tail}” into "
                        f"“{head}” ({len(pages)} page{'s' if len(pages) != 1 else ''})"
                    ),
                    instruction=(
                        f"Folder “{head}” contains nothing but the single-child "
                        f"folder chain down to “{tail}”. Flatten it: move each "
                        f"page with move_page, exactly as listed —\n{moves}\n"
                        "Do not edit any page body. Leave the emptied wrapper "
                        "folders and their .gitkeep markers in place — a "
                        "separate cleanup removes empty folders."
                    ),
                    auto_approvable=False,
                )
            )
        return drafts

    def validate(self, proposal: dict[str, Any]) -> str | None:
        """Premise: the chain still looks exactly as proposed — same links,
        same tail, the same page set under it (a page added since would be
        stranded half-flattened), and every destination still free. Body
        edits to the pages don't break the premise; moves stay content-
        preserving."""
        folders = [s for s in proposal["source_paths"] if not s.endswith(".md")]
        pages = [s for s in proposal["source_paths"] if s.endswith(".md")]
        if not folders:
            return "proposal is missing its chain folders"
        head, tail = min(folders, key=len), max(folders, key=len)
        tracked = list(git.list_paths())
        tree = _Tree(tracked)
        if tree.walk_chain(head) != tail:
            return f"“{head}” is no longer a single-child chain down to “{tail}”"
        current = {p for p in git.list_paths(tail) if not p.endswith("/" + _GITKEEP)}
        if current != set(pages):
            return f"the pages under “{tail}” changed since this was proposed"
        taken_lower = {p.lower() for p in tracked}
        occupied = [t for t in proposal["target_paths"] if t.lower() in taken_lower]
        if occupied:
            return f"destination already exists: {occupied[0]!r}"
        return None


DETECTOR = _FolderChainDetector()
