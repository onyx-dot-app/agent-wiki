"""Exact body-duplicate detector — mechanical, no LLM.

Groups the scope's pages by git blob sha: equal shas mean **byte-identical
bodies**, the strongest duplicate evidence there is — zero false positives on
the "same bytes" claim. Each group emits one ``merge`` proposal retiring the
redundant copies into a survivor. The merged body is the shared content
unchanged, so the proposal's preview is exact; the agentic applier's work is
the identity half (retire + forward), not content.

Byte-equality is deterministic, but *same bytes* ≠ *same document* when the
content is trivial: two pages that both say ``# TODO`` are usually intentional
placeholders for different future documents, not duplicates. Hence
``MIN_BODY_BYTES`` — only substantial identical bodies are proposed; trivial
stubs are the (LLM-judged) stub detector's territory.

This is a **pairing** detector (``pairs_paths = True``): the runner feeds it
one permission bucket at a time, so it never pairs pages across a visibility
boundary — the proposal itself would leak a restricted page's existence.

Survivor choice is a deterministic heuristic: the shallowest path (closer to
the wiki root reads as the more canonical home), then the shortest, then
lexicographic. The reviewer sees which page survives and can reject if the
heuristic picked wrong.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any

from app.wiki import git
from app.wiki.automanage.detectors.base import ProposalDraft, Scope, TriggerKind
from app.wiki.change_proposals import ProposalOp

log = logging.getLogger(__name__)

# Identical bodies below this size are treated as placeholders, not duplicates.
MIN_BODY_BYTES = 120


def _survivor_key(path: str) -> tuple[int, int, str]:
    return (path.count("/"), len(path), path)


class _BodyDupDetector:
    name = "body_dup"
    pairs_paths = True  # runner partitions this detector's scope by audience

    def __init__(self, min_body_bytes: int = MIN_BODY_BYTES):
        self._min_bytes = min_body_bytes

    def applicable(self, trigger: TriggerKind) -> bool:
        return trigger == TriggerKind.SWEEP

    def detect(self, scope: Scope) -> list[ProposalDraft]:
        in_scope = {p for p in scope.paths if p.endswith(".md")}
        if len(in_scope) < 2:
            return []
        by_blob: dict[str, list[str]] = defaultdict(list)
        for path, blob in git.list_paths_with_blob_sha():
            if path in in_scope:
                by_blob[blob].append(path)

        drafts: list[ProposalDraft] = []
        for blob, paths in sorted(by_blob.items()):
            if len(paths) < 2:
                continue
            body = git.read_file_opt(paths[0])
            if body is None or len(body.encode()) < self._min_bytes:
                continue
            ordered = sorted(paths, key=_survivor_key)
            survivor, redundant = ordered[0], ordered[1:]
            drafts.append(
                ProposalDraft(
                    op=ProposalOp.MERGE,
                    source_paths=redundant,
                    target_paths=[survivor],
                    summary=(
                        f"Merge {len(redundant)} byte-identical duplicate"
                        f"{'s' if len(redundant) > 1 else ''} of "
                        f"“{survivor}”: " + ", ".join(f"“{p}”" for p in redundant)
                    ),
                    instruction=(
                        "These pages have byte-identical bodies. Keep "
                        f"{survivor!r} unchanged and retire each duplicate "
                        "into it (trash + identity forward). Do not rewrite "
                        "any content."
                    ),
                    proposed_bodies={survivor: body},
                    # Merges wait for a human until the agentic applier has
                    # earned auto-apply with reliability data.
                    auto_approvable=False,
                )
            )
        return drafts

    def validate(self, proposal: dict[str, Any]) -> str | None:
        """Premise: every affected page still exists and all bodies are still
        byte-identical. An edit that keeps them identical (e.g. the same fix
        applied to every copy) does not invalidate the proposal."""
        paths = proposal["target_paths"] + proposal["source_paths"]
        bodies: list[str] = []
        for path in paths:
            body = git.read_file_opt(path)
            if body is None:
                return f"{path!r} no longer exists"
            bodies.append(body)
        if any(b != bodies[0] for b in bodies[1:]):
            return "the pages are no longer byte-identical"
        return None


DETECTOR = _BodyDupDetector()
