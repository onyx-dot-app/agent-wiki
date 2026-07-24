"""Stub/placeholder-page detector — mechanical, no LLM.

A page that never grew past a title is clutter that pollutes search and
listings: someone created it meaning to write, and didn't. After a grace
window this proposes ``delete_page`` — removal, not merge — for pages whose
body carries essentially no content once scaffolding is stripped.

**Content**, for this detector, is what remains after dropping heading
lines, horizontal rules, and blank lines, with whitespace runs collapsed.
A title-only page measures zero; ``# Notes\\n\\nTODO`` measures four bytes; a
single real sentence clears the floor. The floor is deliberately low
(``STUB_MAX_CONTENT_BYTES``) — this detector exists for pages that are
*obviously* placeholders, not for judging whether short pages are worth
keeping. A skeleton instantiated from a template and partially filled in
(placeholder tokens, a couple of real fields) is *not* a stub — telling
"barely started" from "abandoned scaffold" takes judgment, which is the
future fuzzy detectors' job, not a byte count's.

Division of labor: a page byte-identical to a current template body is the
template-echo detector's case and is skipped here — its proposal names the
template, which is a better review story than "this page is small".

Never auto-approvable: smallness is evidence, not proof — a terse page can
be exactly as long as it needs to be, so a human confirms every removal.
"""
from __future__ import annotations

import logging
import re
from datetime import UTC, datetime, timedelta
from typing import Any

from app.wiki import git
from app.wiki.automanage.detectors.base import ProposalDraft, Scope, TriggerKind
from app.wiki.automanage.detectors.template_echo import (
    blob_sha,
    template_body_blob_shas,
)
from app.wiki.change_proposals import ProposalOp

log = logging.getLogger(__name__)

# A page whose stripped body is at or below this many bytes is a stub. Sized
# to catch "TODO", "Coming soon.", "fill me in later" — and to stay well
# below one meaningful sentence (a short real sentence runs ~35+ bytes).
STUB_MAX_CONTENT_BYTES = 24

# The grace window off the page's last commit: a small page someone touched
# recently may be about to grow. Any edit restarts the clock.
STUB_MIN_AGE_DAYS = 7

_SCAFFOLDING_LINE = re.compile(r"^\s*(#{1,6}\s.*|#{1,6}|-{3,}|_{3,}|\*{3,})\s*$")


def content_bytes(body: str) -> int:
    """Byte size of ``body`` once scaffolding is stripped: heading lines,
    horizontal rules, and blank lines dropped; whitespace runs collapsed to
    one space. What's left is prose, links, list items, code — anything a
    reader actually came for."""
    kept = [
        line for line in body.splitlines() if not _SCAFFOLDING_LINE.match(line)
    ]
    return len(re.sub(r"\s+", " ", " ".join(kept)).strip().encode())


def _old_enough(page: str, now: datetime, min_age_days: int) -> bool:
    meta = git.last_commit_meta_for_path(page)
    if meta is None:
        return False
    try:
        touched = datetime.fromisoformat(meta[2])
    except ValueError:
        log.warning("stub-page: unparseable commit ts %r for %s", meta[2], page)
        return False
    return now - touched >= timedelta(days=min_age_days)


class _StubPageDetector:
    name = "stub_page"
    pairs_paths = False  # judges each page alone; sees the whole scope

    def __init__(
        self,
        *,
        max_content_bytes: int = STUB_MAX_CONTENT_BYTES,
        min_age_days: int = STUB_MIN_AGE_DAYS,
    ) -> None:
        self.max_content_bytes = max_content_bytes
        self.min_age_days = min_age_days

    def applicable(self, trigger: TriggerKind) -> bool:
        # Sweep-only: the age gate means a page can't qualify at write time.
        return trigger == TriggerKind.SWEEP

    def _is_stub(self, body: str) -> bool:
        return content_bytes(body) <= self.max_content_bytes

    def detect(self, scope: Scope) -> list[ProposalDraft]:
        if not self.applicable(scope.trigger):
            return []
        pages = sorted(p for p in scope.paths if p.endswith(".md"))
        if not pages:
            return []
        echoes = template_body_blob_shas()
        now = datetime.now(UTC)
        drafts: list[ProposalDraft] = []
        for path in pages:
            body = git.read_file_opt(path)
            if body is None or not self._is_stub(body):
                continue
            if blob_sha(body) in echoes:
                continue  # template-echo's case — better review story there
            if not _old_enough(path, now, self.min_age_days):
                continue
            drafts.append(
                ProposalDraft(
                    op=ProposalOp.DELETE_PAGE,
                    source_paths=[path],
                    summary=(
                        f"Remove stub page “{path}” — no real content after "
                        f"{self.min_age_days}+ days"
                    ),
                    # State the premise exactly: "at most a few words",
                    # not "title/scaffolding only" — a tiny page whose bytes
                    # are all body text is still a stub, and the applier
                    # shouldn't re-litigate the reviewer's judgment over a
                    # wording mismatch (premise drift is validate()'s job,
                    # re-checked before the applier ever runs).
                    instruction=(
                        f"The page {path!r} is a stub: at most a few words "
                        "of content, unchanged long enough to be abandoned. "
                        "A reviewer approved its removal and a validation "
                        "step has re-confirmed it is still a stub. Remove it "
                        "with trash_page (restorable). Do not write any "
                        "content."
                    ),
                    auto_approvable=False,
                )
            )
        return drafts

    def validate(self, proposal: dict[str, Any]) -> str | None:
        """Premise: the page still has no real content. Someone writing even
        a sentence since approval clears the floor and stales the proposal;
        scaffolding-only shuffles keep it valid."""
        path = proposal["source_paths"][0]
        body = git.read_file_opt(path)
        if body is None:
            return f"{path!r} no longer exists"
        if not self._is_stub(body):
            return f"“{path}” has content now — no longer a stub"
        return None


DETECTOR = _StubPageDetector()
