"""Misplaced-page detector — LLM detector #2, deliberately narrow.

Proposes filing **root-dumped stray pages** into an existing folder — and
nothing else. v1 scope is strays-only on purpose: no deep re-filing, no new
folders, no restructuring; a page qualifies only when it evidently doesn't
belong at the root AND exactly one existing folder is clearly its home
(both argued from the page's content, not name resemblance). Moves are the
safest verb to be conservative with — links, ids, permissions, and comments
follow the page, and a wrong move is one move back — but a wrong proposal
still costs reviewer trust, so every rail from the stale-page doctrine
applies. Design: the Detection wiki page.

Mechanical rails around the LLM's judgment:
- candidates = root-level pages only, edit-quiet for the floor window, and
  never the root-dwelling entry points (readme/home/index/start-here);
- a proposed destination must be an existing folder in the same audience
  bucket that already holds pages, and the target path must be free
  (case-insensitively, across the whole tree);
- at most ``MAX_PROPOSALS`` per sweep; never auto-approvable; LLM failure
  degrades to an empty result.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from app.llm.errors import LLMError
from app.llm.prompts import load_prompt
from app.wiki import git
from app.wiki.automanage.detectors import llm_agent
from app.wiki.automanage.detectors.base import ProposalDraft, Scope, TriggerKind
from app.wiki.filesystem import is_page
from app.wiki.change_proposals import ProposalOp

log = logging.getLogger(__name__)

# Don't propose moving a page someone is actively editing.
FLOOR_DAYS = 30
# Even lower than stale-page's: filing suggestions are pure judgment, so the
# drip is slower while the detector earns trust.
MAX_PROPOSALS = 2

# Root-dwelling by convention — never candidates, whatever their age.
_ROOT_ENTRY_POINTS = frozenset(
    {"readme", "home", "index", "start here", "start-here"}
)

_FINISH_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "proposals": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "dest_folder": {
                        "type": "string",
                        "description": "An EXISTING folder from the tree.",
                    },
                    "evidence": {
                        "type": "string",
                        "description": "One sentence a reviewer can judge "
                        "at a glance.",
                    },
                },
                "required": ["path", "dest_folder", "evidence"],
            },
        }
    },
    "required": ["proposals"],
}


def _now() -> datetime:
    return datetime.now(UTC)


class _MisplacedPageDetector:
    name = "misplaced_page"
    # Audience isolation (see stale_page): the tree shown to the model, the
    # search results, and the destination folders all stay within one
    # same-audience bucket.
    pairs_paths = True

    def __init__(self, floor_days: int = FLOOR_DAYS):
        self._floor_days = floor_days

    def applicable(self, trigger: TriggerKind) -> bool:
        # Sweep-only: filing is whole-tree judgment; the on-create trigger's
        # neighborhood scope can't see the destination folders.
        return trigger == TriggerKind.SWEEP

    # ---- mechanical prefilter ------------------------------------------

    def _folders(self, scope: Scope) -> dict[str, int]:
        """Existing folders in this bucket → page count (destinations must
        already hold real pages)."""
        counts: dict[str, int] = {}
        for p in scope.paths:
            if not is_page(p) or "/" not in p:
                continue
            folder = p.rsplit("/", 1)[0]
            counts[folder] = counts.get(folder, 0) + 1
        return counts

    def _candidates(self, scope: Scope) -> list[str]:
        root_pages = sorted(
            p for p in scope.paths if is_page(p) and "/" not in p
        )
        root_pages = [
            p
            for p in root_pages
            if p[: -len(".md")].lower() not in _ROOT_ENTRY_POINTS
        ]
        if not root_pages or not self._folders(scope):
            return []
        floor_dt = _now() - timedelta(days=self._floor_days)
        # Explicit UTC offset — git --since parses bare timestamps in the
        # process's local timezone (see stale_page).
        recently_edited = git.paths_touched_since(
            floor_dt.strftime("%Y-%m-%dT%H:%M:%S+00:00")
        )
        return [p for p in root_pages if p not in recently_edited]

    # ---- agent pass -----------------------------------------------------

    def _agent_pass(
        self, scope: Scope, candidates: list[str]
    ) -> list[dict[str, str]]:
        tree = "\n".join(sorted(p for p in scope.paths))
        system = load_prompt("misplaced_page_detect.system").replace(
            "{max_proposals}", str(MAX_PROPOSALS)
        )
        raw = llm_agent.run_agent(
            detector_name=self.name,
            system=system,
            user_content=(
                "Wiki file tree:\n" + tree + "\n\nCandidate pages (loose at "
                "the wiki root, not edited recently):\n" + "\n".join(candidates)
            ),
            candidates=set(candidates),
            audience_fp=llm_agent.bucket_fingerprint(candidates),
            finish_schema=_FINISH_SCHEMA,
        )
        folders = self._folders(scope)
        taken_lower = {p.lower() for p in git.list_paths()}
        candidate_set = set(candidates)
        picked: list[dict[str, str]] = []
        for item in raw[:MAX_PROPOSALS]:
            path = str(item.get("path", ""))
            dest_folder = str(item.get("dest_folder", "")).strip().strip("/")
            if path not in candidate_set:
                log.warning(
                    "misplaced_page: non-candidate %r — dropped", path
                )
                continue
            if folders.get(dest_folder, 0) < 1:
                # Not an existing same-audience folder with real pages —
                # includes invented folders and cross-bucket destinations.
                log.warning(
                    "misplaced_page: destination %r is not an existing "
                    "folder in this bucket — dropped",
                    dest_folder,
                )
                continue
            target = f"{dest_folder}/{path}"
            if target.lower() in taken_lower:
                log.warning(
                    "misplaced_page: target %r already exists — dropped",
                    target,
                )
                continue
            picked.append(
                {
                    "path": path,
                    "target": target,
                    "evidence": str(item.get("evidence", "")),
                }
            )
        return picked

    # ---- detector protocol ----------------------------------------------

    def detect(self, scope: Scope) -> list[ProposalDraft]:
        candidates = self._candidates(scope)
        if not candidates:
            return []
        try:
            picked = self._agent_pass(scope, candidates)
        except LLMError as e:
            log.warning("misplaced_page: LLM pass skipped: %s", e)
            return []
        drafts: list[ProposalDraft] = []
        for item in picked:
            path, target, evidence = item["path"], item["target"], item["evidence"]
            meta = git.last_commit_meta_for_path(path)
            if meta is None:
                continue
            drafts.append(
                ProposalDraft(
                    op=ProposalOp.MOVE,
                    source_paths=[path],
                    target_paths=[target],
                    summary=(
                        f"Move “{path}” into “{target.rsplit('/', 1)[0]}/” — "
                        f"{evidence}"
                    ),
                    instruction=(
                        f"File this stray page: move_page {path!r} to "
                        f"{target!r}, exactly that one move. Do not edit any "
                        "page body; links, permissions, and comments follow "
                        "the page."
                    ),
                    # Premise: the content the filing judgment was made over.
                    # An edit voids it — re-filing changed content is a fresh
                    # ask, not a rejected recurrence.
                    premise=meta[0],
                    # Filing is judgment; it always gets a human.
                    auto_approvable=False,
                )
            )
        return drafts

    def validate(self, proposal: dict[str, Any]) -> str | None:
        """Mechanical only (never the LLM): the page must be unchanged and
        the destination still viable."""
        (path,) = proposal["source_paths"]
        (target,) = proposal["target_paths"]
        if git.read_file_opt(path) is None:
            return f"{path!r} no longer exists"
        meta = git.last_commit_meta_for_path(path)
        if meta is None:
            return f"{path!r} no longer exists"
        anchors = cast("dict[str, str]", proposal.get("base_shas") or {})
        if meta[0] != anchors.get(path):
            return "the page changed since it was judged misplaced"
        live = git.list_paths()
        if target.lower() in {p.lower() for p in live}:
            return "the destination path is taken now"
        dest_folder = target.rsplit("/", 1)[0] + "/"
        if not any(p.startswith(dest_folder) for p in live):
            return "the destination folder no longer holds any pages"
        return None


DETECTOR = _MisplacedPageDetector()
