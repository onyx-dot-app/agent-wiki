"""Stale-page detector — the first LLM detector, agentic.

Recommends deletion for pages that are stale AND demonstrably not useful.
Time is the filter; uselessness is the claim: the mechanical prefilter only
admits pages with neither an edit (git) nor a recorded view (``page_views``)
inside the floor window, and the LLM agent may only propose a page after
reading it and finding uselessness evidence in the content (plus optional
wiki searches for coverage elsewhere). Two categories only — time-bound
coordination shells with no record value (bare agendas, event logistics,
unfilled skeletons; anything that RECORDS what happened is an archive and
never proposed), and test/scratch debris; evergreen-looking content is never
proposed on age alone. Design: the "Stale-page detector" section of the
Detection wiki page.

Conservative rails, all constants below: 30-day floor, at most 3 proposals
per sweep, bounded reads/searches/LLM calls, never auto-approvable. A page
with no view row only counts as unviewed once tracking itself is older than
the floor (``page_views.tracking_floor``). LLM unavailability degrades to an
empty result — one broken detector must not sink the sweep's mechanical
detectors.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from app.llm.errors import LLMError
from app.llm.prompts import load_prompt
from app.wiki import git, page_views
from app.wiki.automanage.detectors import llm_agent
from app.wiki.automanage.detectors.base import ProposalDraft, Scope, TriggerKind
from app.wiki.filesystem import is_page
from app.wiki.change_proposals import ProposalOp

log = logging.getLogger(__name__)

# Deliberately short: the floor only keeps fresh/in-use pages away from the
# LLM; the conservatism lives in the evidence requirements of the prompt.
FLOOR_DAYS = 30
# A slow drip builds reviewer trust; a flood of delete cards destroys it.
MAX_PROPOSALS = 3
MAX_CANDIDATES = 100

_FINISH_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "proposals": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "evidence": {
                        "type": "string",
                        "description": "One sentence a reviewer can judge "
                        "at a glance.",
                    },
                },
                "required": ["path", "evidence"],
            },
        }
    },
    "required": ["proposals"],
}


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")


class _StalePageDetector:
    name = "stale_page"
    # Audience isolation, not pairing: the runner feeds one same-audience
    # bucket at a time, so the file tree shown to the model, the search
    # results, and any evidence text can never name a page across a
    # visibility boundary (a proposal's reviewers are cleared for the
    # candidate page, not necessarily for others). Accepted blind spot,
    # same as the pairing detectors': a lone page in its own audience
    # bucket is never scanned.
    pairs_paths = True

    def __init__(self, floor_days: int = FLOOR_DAYS):
        self._floor_days = floor_days

    def applicable(self, trigger: TriggerKind) -> bool:
        # Sweep-only: staleness is a whole-space judgment over ages — there
        # is no creation- or edit-moment at which it becomes newly true.
        return trigger == TriggerKind.SWEEP

    # ---- mechanical prefilter ------------------------------------------

    def _candidates(self, scope: Scope) -> list[str]:
        pages = sorted(p for p in scope.paths if is_page(p))
        if not pages:
            return []
        floor_dt = _now() - timedelta(days=self._floor_days)
        floor = _iso(floor_dt)
        # Explicit UTC offset: git --since parses a bare timestamp in the
        # process's LOCAL timezone, which silently shifts the window on any
        # non-UTC host (and made this prefilter a no-op in local dev).
        recently_edited = git.paths_touched_since(
            floor_dt.strftime("%Y-%m-%dT%H:%M:%S+00:00")
        )
        aged = [p for p in pages if p not in recently_edited]
        if not aged:
            return []
        views = page_views.last_viewed(aged)
        tracking = page_views.tracking_floor()
        tracking_old_enough = tracking is not None and tracking < floor
        out: list[str] = []
        for p in aged:
            seen = views.get(p)
            if seen is not None and seen >= floor:
                continue  # viewed recently — in use
            if seen is None and not tracking_old_enough:
                continue  # tracking too young to call "unviewed"
            out.append(p)
        return out[:MAX_CANDIDATES]

    # ---- agent pass -----------------------------------------------------

    def _agent_pass(
        self, scope: Scope, candidates: list[str]
    ) -> list[dict[str, str]]:
        # Pages only. The scope also carries the `.gitkeep` markers that
        # materialize folders and any `.trigger_*.yaml` — the folder
        # detectors need those, but they are not content the model can
        # reason about, and on a real wiki they were a quarter of every
        # tree it read. Folders holding pages still appear, since a page
        # path carries its prefix.
        tree = "\n".join(sorted(p for p in scope.paths if is_page(p)))
        ages: list[str] = []
        for p in candidates:
            meta = git.last_commit_meta_for_path(p)
            ages.append(f"{p} (last edit {meta[2][:10] if meta else 'unknown'})")
        system = load_prompt("stale_page_detect.system").replace(
            "{max_proposals}", str(MAX_PROPOSALS)
        )
        candidate_set = set(candidates)
        raw = llm_agent.run_agent(
            detector_name=self.name,
            system=system,
            user_content=(
                "Wiki file tree:\n" + tree + "\n\nCandidate pages "
                f"(no edit and no recorded view in {self._floor_days}+ days):\n"
                + "\n".join(ages)
            ),
            candidates=candidate_set,
            audience_fp=llm_agent.bucket_fingerprint(candidates),
            finish_schema=_FINISH_SCHEMA,
        )
        picked: list[dict[str, str]] = []
        for item in raw[:MAX_PROPOSALS]:
            path = str(item.get("path", ""))
            if path not in candidate_set:
                log.warning(
                    "stale_page: model proposed non-candidate %r — dropped", path
                )
                continue
            picked.append(
                {"path": path, "evidence": str(item.get("evidence", ""))}
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
            # One unconfigured/broken LLM must not sink the sweep's
            # mechanical detectors.
            log.warning("stale_page: LLM pass skipped: %s", e)
            return []
        drafts: list[ProposalDraft] = []
        for item in picked:
            path, evidence = item["path"], item["evidence"]
            meta = git.last_commit_meta_for_path(path)
            if meta is None:
                continue
            drafts.append(
                ProposalDraft(
                    op=ProposalOp.DELETE_PAGE,
                    source_paths=[path],
                    target_paths=[],
                    summary=f"Delete stale page “{path}” — {evidence}",
                    instruction=(
                        "This page was judged stale and not useful "
                        f"({evidence}). Remove it with trash_page (restorable "
                        "from Trash). Do not touch any other page."
                    ),
                    # Premise: the content the judgment was made over. An edit
                    # resets staleness — same page stale again LATER with new
                    # content is a fresh ask, not a rejected recurrence.
                    premise=meta[0],
                    # Deletions always get a human, even in AI-managed scopes.
                    auto_approvable=False,
                )
            )
        return drafts

    def validate(self, proposal: dict[str, Any]) -> str | None:
        """LLM-judged detector ⇒ mechanical proxy only (never the LLM): any
        activity since the judgment voids it — an edit (premise sha drift) or
        a recorded view newer than the proposal's own timestamp."""
        (path,) = proposal["source_paths"]
        if git.read_file_opt(path) is None:
            return f"{path!r} no longer exists"
        meta = git.last_commit_meta_for_path(path)
        if meta is None:
            return f"{path!r} no longer exists"
        anchors = cast("dict[str, str]", proposal.get("base_shas") or {})
        if meta[0] != anchors.get(path):
            return "the page changed since it was judged stale"
        seen = page_views.last_viewed([path]).get(path)
        anchor = proposal.get("last_emitted_at") or proposal.get("created_at")
        if seen is not None and anchor is not None and seen > anchor:
            return "the page was viewed since it was judged stale"
        return None


DETECTOR = _StalePageDetector()
