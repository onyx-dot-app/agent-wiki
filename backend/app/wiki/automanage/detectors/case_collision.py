"""Case-insensitive path-collision detector — mechanical, no LLM.

Two pages whose paths differ only by letter case (``docs/Setup.md`` vs
``docs/setup.md``) are a correctness hazard, not just clutter: git tracks
both, but case-insensitive filesystems (macOS and Windows defaults) can
materialize only one — a clone, backup restore, or export silently loses the
other's working copy. Links and search get ambiguous for humans, too.

The proposal consents to a **set of outcomes** and the applier — which
reads both pages at execution time — chooses within it: genuinely distinct
documents get a **rename** (content preserved, hazard gone); two versions of
the same document get a **merge** into the kept page (unique content folded
in, the loser retired with identity forwarding). Byte-identical groups are
still skipped here — body-dup's merge already covers them exactly.

The kept page is the same survivor heuristic as body-dup (shallowest, then
shortest, then lexicographic); the rename option's new name is the loser's
path with a numeric suffix, chosen deterministically to avoid any further
collision. It rides in ``source_paths`` so the rename branch stays inside
the applier's path scope; ``target_paths`` carries the kept page (the side
that must survive either outcome). Naming the kept page in the summary is
audience-safe: this is a pairing detector, so the runner only pairs pages
within one permission bucket.
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
                        source_paths=[loser, new_name],
                        target_paths=[kept],
                        summary=(
                            f"Resolve the name collision between “{loser}” "
                            f"and “{kept}” (case-insensitive filesystems keep "
                            f"only one) — rename it to “{new_name}”, or merge "
                            "it into the kept page if they are two versions "
                            "of the same document"
                        ),
                        instruction=(
                            f"The paths {loser!r} and {kept!r} differ only by "
                            "letter case — a hazard on case-insensitive "
                            "filesystems. Read both pages and choose: if they "
                            "are genuinely distinct documents, rename with "
                            f"move_page({loser!r} -> {new_name!r}) and do not "
                            "modify any content. If they are two versions of "
                            "the same document (similar content AND purpose), "
                            f"fold any unique content of {loser!r} into "
                            f"{kept!r} with write_page, then "
                            f"retire_page({loser!r} -> {kept!r})."
                        ),
                        auto_approvable=False,
                    )
                )
        return drafts

    def validate(self, proposal: dict[str, Any]) -> str | None:
        """Premise: the collision still exists and the rename option's name is
        still free. Content equality is deliberately *not* re-checked — the
        applier's merge branch handles pages that converged while pending."""
        loser, new_name = proposal["source_paths"][:2]
        kept = proposal["target_paths"][0]
        live = list(git.list_paths())
        if loser not in live:
            return f"{loser!r} no longer exists"
        if kept not in live:
            return f"{kept!r} no longer exists"
        if kept.lower() != loser.lower():
            return "the name collision no longer exists"
        if any(p.lower() == new_name.lower() for p in live):
            return f"the rename option {new_name!r} is no longer free"
        return None


DETECTOR = _CaseCollisionDetector()
