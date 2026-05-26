"""Dry-run stub for the trigger eval.

Patches ``app.llm.client.complete`` to return canned responses that
mirror the production tool-call / JSON shape the natural_language module
expects. Routing is by **payload fingerprint** — the union of paths in
``wiki_state`` plus the case's change/scope/new-file path. Both phase-1
and phase-2 calls see the full payload (wiki snapshot + change view),
so the fingerprint resolves to the same case for both phases of one
trial. Cases that share the exact same fingerprint collide and are
logged + routed to the first-seen case rather than raising — keeping
CI smoke deterministic when two cases happen to seed the same wiki.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Generator
from contextlib import contextmanager
from typing import Any

from app.llm import client as llm_client
from app.llm.client import CompletionResult, ToolCall
from app.triggers import natural_language as nl_module

from evals.schema import TriggerCase, TriggerFlavor
from evals.scorers import JUDGE_SYSTEM_MARKER as _JUDGE_MARKER


log = logging.getLogger(__name__)


def _fingerprint(case: TriggerCase) -> tuple[str, ...]:
    """Stable per-case lookup key for the dry-run stub.

    Uses the union of paths the payload will contain — the wiki snapshot
    paths plus whichever flavor-specific path the case carries. Both
    phases of a trial see the same payload, so the fingerprint resolves
    the same case for phase-1 and phase-2 alike.
    """
    paths: list[str] = sorted(d.path for d in case.wiki_state)
    extras = [p for p in (case.change_path, case.scope_path, case.new_file_path) if p is not None]
    for p in extras:
        if p not in paths:
            paths.append(p)
    return tuple(paths)


@contextmanager
def stub_triggers(cases: list[TriggerCase]) -> Generator[None]:
    """Patch ``client.complete`` to canned per-case responses."""
    original = llm_client.complete
    case_by_fp: dict[tuple[str, ...], TriggerCase] = {}
    for case in cases:
        fp = _fingerprint(case)
        if fp in case_by_fp and case_by_fp[fp].id != case.id:
            log.warning(
                "stub_triggers: duplicate payload fingerprint for cases %s and %s; "
                "routing both to first",
                case_by_fp[fp].id,
                case.id,
            )
            continue
        case_by_fp[fp] = case

    # Multi-overlap routing: pick the case whose fingerprint paths are
    # ALL present in the user text and has the most paths (most-specific
    # wins for any future subset overlaps).
    def _route(user_text: str) -> TriggerCase | None:
        best: TriggerCase | None = None
        best_size = -1
        for fp, case in case_by_fp.items():
            if fp and all(p in user_text for p in fp) and len(fp) > best_size:
                best = case
                best_size = len(fp)
        return best

    def _stub(
        messages: list[dict[str, Any]],
        *,
        model: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int = llm_client.DEFAULT_MAX_TOKENS,
    ) -> CompletionResult:
        del model, max_tokens
        # Judge calls reuse the shared scorer panel — the system prompt
        # carries _JUDGE_MARKER. Return a deterministic YES so dry-run
        # message_facts_* scorers register vacuous pass instead of polling
        # an unreachable model and tying to False.
        for m in messages:
            if m.get("role") != "system":
                continue
            sys_content = m.get("content", "")
            if isinstance(sys_content, str) and _JUDGE_MARKER in sys_content:
                return CompletionResult(text="VERDICT: YES | RATIONALE: stub")
        user_text = "\n".join(m.get("content", "") for m in messages if m.get("role") == "user")
        matched_case = _route(user_text)
        if matched_case is None:
            return CompletionResult(text="{}")

        # Renderer call: tools=[render], regardless of flavor.
        if tools and any(t.get("name") == "render" for t in tools):
            msg_text = "STUB MESSAGE for %s" % matched_case.id
            return CompletionResult(
                tool_calls=[ToolCall(id="stub-1", name="render", arguments={"message": msg_text})]
            )

        # New-file path: no tools, JSON response body.
        if matched_case.flavor is TriggerFlavor.NEW_FILE:
            return CompletionResult(
                text=json.dumps(
                    {
                        "triggered": matched_case.expected_matched,
                        "trigger_message": (
                            "STUB NEWFILE for %s" % matched_case.id
                            if matched_case.expected_matched
                            else ""
                        ),
                    }
                )
            )

        # Otherwise it's a phase-1 matches/matches_snapshot call expecting
        # a report tool call.
        return CompletionResult(
            tool_calls=[
                ToolCall(
                    id="stub-1",
                    name="report",
                    arguments={
                        "matches": matched_case.expected_matched,
                        "reason": "stub: %s" % matched_case.id,
                    },
                )
            ]
        )

    # natural_language imports complete by name (``from app.llm.client
    # import complete``), so monkey-patching the client module alone
    # leaves the bound reference untouched. Patch both — client.complete
    # for any future indirect callers, plus the bound name on the
    # natural_language module that the trigger eval actually drives.
    llm_client.complete = _stub  # type: ignore[assignment]
    nl_original = nl_module.complete
    nl_module.complete = _stub  # type: ignore[assignment]
    try:
        yield
    finally:
        llm_client.complete = original  # type: ignore[assignment]
        nl_module.complete = nl_original  # type: ignore[assignment]
