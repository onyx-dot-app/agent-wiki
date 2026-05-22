"""Stub LLM responder used by ``--dry-run`` runs.

Lets the eval harness exercise its full pipeline (dataset → agent code →
scorers → reporting) without an API key. Useful for two things:

1. CI smoke — verify no scorer / runner regression without burning tokens.
2. Local validation that a freshly added case parses and the scorers wire up.

The stub returns:

* The judge prompt (``app.llm.scorers._JUDGE_SYSTEM`` text marker) → "YES",
  so quality scorers register vacuous pass and the trigger scorer is
  exercised independently.
* Anything else → a class-shaped default for the case whose ``current_body``
  appears in the rendered user prompt. Falls back to NO_CHANGE if no case
  matches (defensive — shouldn't happen on a valid dataset).

The lookup matches on ``current_body`` rather than ``wiki_path`` so two
cases can legitimately reuse the same wiki page (e.g. one tests
``IRRELEVANT`` against an unrelated push, another tests ``CHANGE`` against
a relevant one).
"""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from typing import Any

from app.llm import client as llm_client
from app.llm.client import CompletionResult, ToolCall

from evals.schema import TriggerClass, WikiUpdaterCase
from evals.scorers import JUDGE_SYSTEM_MARKER as _JUDGE_MARKER


# Substring length used as a fingerprint of ``current_body``. Long enough to
# uniquely identify a case across the dataset, short enough that minor prompt
# template changes don't break matching.
_BODY_FINGERPRINT_LEN = 200


def _default_response(case: WikiUpdaterCase) -> str:
    """Class-shaped default when a case doesn't ship a ``dry_run_response``."""
    if case.expected_class is TriggerClass.NO_CHANGE:
        return "NO_CHANGE"
    if case.expected_class is TriggerClass.IRRELEVANT:
        return "IRRELEVANT"
    # CHANGE — emit the current body with a short synthesized addition that
    # references each expected fact. Good enough that the judge stub returns
    # YES for all of them; ratio stays inside the bloat budget.
    extras = "\n".join(f"- {f.text}" for f in case.expected_facts_present)
    if extras:
        return f"{case.current_body.rstrip()}\n\n## Updates\n\n{extras}\n"
    return case.current_body


def _user_text(messages: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for m in messages:
        if m.get("role") != "user":
            continue
        content = m.get("content", "")
        if isinstance(content, str):
            parts.append(content)
    return "\n".join(parts)


def _is_judge_call(messages: list[dict[str, Any]]) -> bool:
    for m in messages:
        if m.get("role") != "system":
            continue
        content = m.get("content", "")
        if isinstance(content, str) and _JUDGE_MARKER in content:
            return True
    return False


def _case_fingerprint(case: WikiUpdaterCase) -> str:
    return case.current_body[:_BODY_FINGERPRINT_LEN]


def _submit_results_tool_call(case: WikiUpdaterCase | None) -> ToolCall:
    """Build a single-candidate `submit_results` tool call matching the case.

    The production reconciler runs batches but the eval drives one case per
    `batch_reconcile` invocation, so the results array has exactly one entry.
    """
    if case is None or case.expected_class is TriggerClass.IRRELEVANT:
        action_args: dict[str, Any] = {"candidate_index": 1, "action": "irrelevant"}
    elif case.expected_class is TriggerClass.NO_CHANGE:
        action_args = {"candidate_index": 1, "action": "no_change"}
    else:
        # CHANGE — emit one find/replace edit that appends an Updates section
        # listing each expected fact. `find` is the trailing newline so the
        # edit is non-destructive; `replace` carries the synthesized addition.
        body = case.current_body
        tail = body[-1] if body else "\n"
        extras = "\n".join(f"- {f.text}" for f in case.expected_facts_present)
        replacement = (tail + "\n## Updates\n\n" + extras + "\n") if extras else body
        action_args = {
            "candidate_index": 1,
            "action": "edit",
            "edits": [{"find": tail, "replace": replacement}],
        }
    return ToolCall(
        id="stub-submit-results",
        name="submit_results",
        arguments={"results": [action_args]},
    )


@contextmanager
def stub_completions(cases: list[WikiUpdaterCase]) -> Generator[None]:
    """Patch ``app.llm.client.complete`` to return canned per-case responses.

    Doesn't touch ``stream`` — the agents under eval only use ``complete``.
    """
    case_by_fingerprint: dict[str, WikiUpdaterCase] = {}
    for case in cases:
        fingerprint = _case_fingerprint(case)
        existing = case_by_fingerprint.get(fingerprint)
        if existing is not None and existing.id != case.id:
            raise ValueError(
                "stub fingerprint collision between cases %s and %s "
                "(first %d chars of current_body match)"
                % (existing.id, case.id, _BODY_FINGERPRINT_LEN)
            )
        case_by_fingerprint[fingerprint] = case
    original = llm_client.complete

    def _stub(
        messages: list[dict[str, Any]],
        *,
        model: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int = llm_client.DEFAULT_MAX_TOKENS,
    ) -> CompletionResult:
        del model, max_tokens  # unused in stub
        if _is_judge_call(messages):
            return CompletionResult(text="VERDICT: YES | RATIONALE: stub")
        user_text = _user_text(messages)
        matched = next(
            (c for fp, c in case_by_fingerprint.items() if fp and fp in user_text),
            None,
        )
        # The ingest reconciler now drives the model through a `submit_results`
        # tool call instead of a text body. Detect that contract and craft a
        # matching ToolCall so the production parser produces the expected
        # per-candidate outcome.
        if tools and any(t.get("name") == "submit_results" for t in tools):
            return CompletionResult(tool_calls=[_submit_results_tool_call(matched)])
        if matched is None:
            return CompletionResult(text="NO_CHANGE")
        return CompletionResult(text=_default_response(matched))

    llm_client.complete = _stub  # type: ignore[assignment]
    try:
        yield
    finally:
        llm_client.complete = original  # type: ignore[assignment]
