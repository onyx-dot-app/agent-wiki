"""Case-insensitive path-collision detector — mechanical, no LLM.

Two pages whose paths differ only by letter case (``docs/Setup.md`` vs
``docs/setup.md``) are a correctness hazard, not just clutter: git tracks
both, but case-insensitive filesystems (macOS and Windows defaults) can
materialize only one — a clone, backup restore, or export silently loses the
other's working copy. Links and search get ambiguous for humans, too.

The proposal is a **rename** of the non-canonical page (content preserved,
hazard gone). If the colliding pages are *byte-identical* they're really one
document — the body-duplicate detector's merge resolves the collision by
retiring one — so identical groups are skipped here (precedence, mirroring
the template-echo rule).

The kept page is the same survivor heuristic as body-dup (shallowest, then
shortest, then lexicographic); the rename target is the loser's path with a
numeric suffix, chosen deterministically to avoid any further collision.
Naming the kept page in the summary is audience-safe: this is a pairing
detector, so the runner only pairs pages within one permission bucket.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any

from app.wiki import git
from app.wiki.automanage.detectors.base import ProposalDraft, Scope, TriggerKind
from app.wiki.change_proposals import ProposalOp

log = logging.getLogger(__name__)


def _survivor_key(path: str) -> tuple[int, int, str]:
    return (path.count("/"), len(path), path)


def _deconflicted_name(path: str, taken_lower: set[str]) -> str:
    """``docs/setup.md`` → ``docs/setup-2.md`` (first free suffix, judged
    case-insensitively so the new name can't start a new collision)."""
    stem, dot, ext = path.rpartition(".")
    for i in range(2, 100):
        candidate = f"{stem}-{i}{dot}{ext}"
        if candidate.lower() not in taken_lower:
            return candidate
    raise ValueError(f"no free deconflicted name for {path!r}")


class _CaseCollisionDetector:
    name = "case_collision"
    pairs_paths = True  # names both colliding pages; pair within one audience

    def applicable(self, trigger: TriggerKind) -> bool:
        return trigger == TriggerKind.SWEEP

    def detect(self, scope: Scope) -> list[ProposalDraft]:
        pages = {p for p in scope.paths if p.endswith(".md")}
        if len(pages) < 2:
            return []
        by_lower: dict[str, list[str]] = defaultdict(list)
        for p in pages:
            by_lower[p.lower()].append(p)
        collisions = {k: v for k, v in by_lower.items() if len(v) > 1}
        if not collisions:
            return []

        blobs = dict(git.list_paths_with_blob_sha())
        taken_lower = {p.lower() for p in git.list_paths()}
        drafts: list[ProposalDraft] = []
        for _, group in sorted(collisions.items()):
            ordered = sorted(group, key=_survivor_key)
            kept = ordered[0]
            for loser in ordered[1:]:
                if blobs.get(loser) == blobs.get(kept):
                    # Byte-identical: really one document — body-dup's merge
                    # retires one copy and the collision goes with it.
                    continue
                new_name = _deconflicted_name(loser, taken_lower)
                taken_lower.add(new_name.lower())
                drafts.append(
                    ProposalDraft(
                        op=ProposalOp.RENAME,
                        source_paths=[loser],
                        target_paths=[new_name],
                        summary=(
                            f"Rename “{loser}” to “{new_name}” — its name "
                            f"collides with “{kept}” on case-insensitive "
                            "filesystems (macOS/Windows checkouts keep only "
                            "one of them)"
                        ),
                        instruction=(
                            f"Rename {loser!r} to {new_name!r} with move_page "
                            "— its path differs from a distinct page only by "
                            "letter case. Do not modify any content."
                        ),
                        auto_approvable=False,
                    )
                )
        return drafts

    def validate(self, proposal: dict[str, Any]) -> str | None:
        """Premise: the page still exists, still case-collides with another
        live page of *different* content, and the rename target is still
        free (case-insensitively)."""
        loser = proposal["source_paths"][0]
        new_name = proposal["target_paths"][0]
        live = list(git.list_paths())
        if loser not in live:
            return f"{loser!r} no longer exists"
        partners = [
            p for p in live if p.lower() == loser.lower() and p != loser
        ]
        if not partners:
            return "the name collision no longer exists"
        blobs = dict(git.list_paths_with_blob_sha())
        if all(blobs.get(p) == blobs.get(loser) for p in partners):
            return (
                "the colliding pages are now byte-identical — a duplicate "
                "merge resolves this instead"
            )
        if any(p.lower() == new_name.lower() for p in live):
            return f"the rename target {new_name!r} is no longer free"
        return None


DETECTOR = _CaseCollisionDetector()
