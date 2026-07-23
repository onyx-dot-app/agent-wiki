"""Template-echo detector — mechanical, no LLM.

A page whose body is still byte-identical to the template it was created from
is a page nobody ever filled in. Equality implies provenance here because the
product's creation flows are template-driven (the new-doc picker and
write_doc(template_id) both instantiate a template, Blank included); the
one template-free path — ingestion — writes connector content that never
matches a template skeleton. After a grace window (someone may be about
to write), it proposes ``delete_page``: removal, not merge — an untouched
template instance has no unique content worth consolidating.

Matching is by git blob sha computed over the template bodies, so the sweep
compares hashes against the tracked tree without reading page bodies. The
grace window keys off the page's *last commit*: any edit both restarts the
clock and (almost certainly) breaks the byte-equality anyway.

This detector also gives the exact body-duplicate detector its precedence
rule: duplicate groups whose shared body is a template body are *echo groups*
— every member should be removed, not merged into each other — so body-dup
skips them (see ``template_body_blob_shas``).
"""
from __future__ import annotations

import hashlib
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from app.wiki import git, templates
from app.wiki.automanage.detectors.base import ProposalDraft, Scope, TriggerKind
from app.wiki.change_proposals import ProposalOp

log = logging.getLogger(__name__)

# The grace window before an untouched template instance is proposed for
# removal — a fresh instantiation is usually someone about to write.
TEMPLATE_ECHO_MIN_AGE_DAYS = 7


def blob_sha(body: str) -> str:
    """Git's content hash for ``body`` — comparable against ``ls-tree`` output
    without writing the content anywhere."""
    raw = body.encode()
    return hashlib.sha1(b"blob %d\0" % len(raw) + raw).hexdigest()


def template_body_blob_shas() -> dict[str, str]:
    """``{blob_sha: template_name}`` over the current templates. Empty bodies
    are skipped — an empty template matches nothing meaningfully."""
    out: dict[str, str] = {}
    for t in templates.list_all():
        body = t.get("body") or ""
        if body.strip():
            out[blob_sha(body)] = t["name"]
    return out


def _old_enough(page: str, now: datetime, min_age_days: int) -> bool:
    meta = git.last_commit_meta_for_path(page)
    if meta is None:
        return False
    try:
        touched = datetime.fromisoformat(meta[2])
    except ValueError:
        log.warning("template-echo: unparseable commit ts %r for %s", meta[2], page)
        return False
    return now - touched >= timedelta(days=min_age_days)


class _TemplateEchoDetector:
    name = "template_echo"
    pairs_paths = False  # compares pages against templates, never against pages

    def __init__(self, *, min_age_days: int = TEMPLATE_ECHO_MIN_AGE_DAYS) -> None:
        self.min_age_days = min_age_days

    def applicable(self, trigger: TriggerKind) -> bool:
        return trigger == TriggerKind.SWEEP

    def detect(self, scope: Scope) -> list[ProposalDraft]:
        echoes = template_body_blob_shas()
        if not echoes:
            return []
        in_scope = {p for p in scope.paths if p.endswith(".md")}
        if not in_scope:
            return []
        now = datetime.now(UTC)
        drafts: list[ProposalDraft] = []
        for path, blob in git.list_paths_with_blob_sha():
            template_name = echoes.get(blob)
            if template_name is None or path not in in_scope:
                continue
            if not _old_enough(path, now, self.min_age_days):
                continue
            drafts.append(
                ProposalDraft(
                    op=ProposalOp.DELETE_PAGE,
                    source_paths=[path],
                    summary=(
                        f"Remove “{path}” — still identical to the "
                        f"“{template_name}” template after "
                        f"{self.min_age_days}+ days (never filled in)"
                    ),
                    instruction=(
                        f"The page {path!r} is an untouched instance of the "
                        f"{template_name!r} template. Remove it with "
                        "trash_page (restorable). Do not write any content."
                    ),
                    auto_approvable=False,
                )
            )
        return drafts

    def validate(self, proposal: dict[str, Any]) -> str | None:
        """Premise: the page still byte-matches a *current* template body.
        One edit by anyone — to the page — breaks equality and invalidates;
        template edits/deletions invalidate too (the page is no longer an
        echo of anything that exists)."""
        path = proposal["source_paths"][0]
        body = git.read_file_opt(path)
        if body is None:
            return f"{path!r} no longer exists"
        if blob_sha(body) not in template_body_blob_shas():
            return "the page no longer matches any template body"
        return None


DETECTOR = _TemplateEchoDetector()
