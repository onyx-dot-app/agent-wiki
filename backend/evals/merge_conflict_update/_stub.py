"""Dry-run stub for the merge_conflict eval.

Patches both ``app.llm.client.complete`` and the agent module's bound
``complete`` reference (the agent does ``from app.llm import client``
then ``client.complete(...)`` — patching ``llm_client.complete`` alone
suffices, but for parity with the trigger eval stub we also patch the
agent module). The stub returns the case's draft body concatenated with
a marker line per current-only fact, so the eval pipeline (scorer pass +
JSONL write) exercises end-to-end without an API key.

Routing: same payload-fingerprint pattern as other stubs — uses
``wiki_path`` + the first 200 chars of ``base_body`` because the agent's
user message embeds both. Collisions are logged and routed to the
first-seen case.
"""

from __future__ import annotations

import logging
from collections.abc import Generator
from contextlib import contextmanager
from typing import Any

from app.llm import client as llm_client
from app.llm.client import CompletionResult

from evals.scorers import JUDGE_SYSTEM_MARKER as _JUDGE_MARKER
from evals.schema import MergeConflictCase

log = logging.getLogger(__name__)


def _fingerprint(case: MergeConflictCase) -> str:
    # The agent's user_msg carries base_body verbatim under "## Base"; it
    # does NOT include wiki_path, so any wiki_path-derived key wouldn't
    # be findable in the prompt text the stub sees. base_body[:200] is
    # the longest substring guaranteed to be uniquely present.
    return case.base_body[:200]


@contextmanager
def stub_merge_conflict(cases: list[MergeConflictCase]) -> Generator[None]:
    """Patch ``client.complete`` to return a deterministic merged body."""
    original = llm_client.complete
    case_by_fp: dict[str, MergeConflictCase] = {}
    for case in cases:
        fp = _fingerprint(case)
        if fp in case_by_fp and case_by_fp[fp].id != case.id:
            log.warning(
                "stub_merge_conflict: duplicate fingerprint for cases %s and %s; routing both to first",
                case_by_fp[fp].id,
                case.id,
            )
            continue
        case_by_fp[fp] = case

    def _route(user_text: str) -> MergeConflictCase | None:
        for fp, case in case_by_fp.items():
            if fp and fp in user_text:
                return case
        return None

    def _stub(
        messages: list[dict[str, Any]],
        *,
        model: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int = llm_client.DEFAULT_MAX_TOKENS,
    ) -> CompletionResult:
        del model, tools, max_tokens
        # Judge calls — return YES so message_facts_* scorers pass under dry-run.
        for m in messages:
            if m.get("role") != "system":
                continue
            sys_content = m.get("content", "")
            if isinstance(sys_content, str) and _JUDGE_MARKER in sys_content:
                return CompletionResult(text="VERDICT: YES | RATIONALE: stub")
        user_text = "\n".join(m.get("content", "") for m in messages if m.get("role") == "user")
        case = _route(user_text)
        if case is None:
            return CompletionResult(text="")
        # Concatenate draft + a "merged from current" tail so both sides'
        # facts appear in the body. When the case expects a conflict
        # annotation, emit the marker the scorer recognises — mirroring
        # the production agent, which uses the commit message when present
        # and falls back to "another update" otherwise.
        annotation = ""
        if case.expects_conflict_annotation:
            source = case.current_commit_message or "another update"
            annotation = "\n\n<!-- conflict from: %s -->" % source
        merged = "%s%s\n\n%s" % (case.draft_body.rstrip(), annotation, case.current_body)
        return CompletionResult(text=merged)

    llm_client.complete = _stub  # type: ignore[assignment]
    try:
        yield
    finally:
        llm_client.complete = original  # type: ignore[assignment]
