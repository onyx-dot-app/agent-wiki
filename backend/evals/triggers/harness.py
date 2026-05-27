"""Synthetic-wiki driver for the trigger eval surface.

Loads ``TriggerCase`` YAMLs, builds the same payload string the production
fan-out hands to ``natural_language.matches`` / ``render_message`` /
``matches_snapshot`` / ``render_snapshot_message`` / ``evaluate_new_file_in_dir``,
and runs the appropriate path. No DB, no git — payload is composed inline
from the case's ``wiki_state`` so the harness is self-contained.

The payload format is identical to ``app.triggers.diff`` so any prompt
tuning that targets the live evaluator is faithfully exercised here.
"""

from __future__ import annotations

import difflib
import logging
from pathlib import Path

import yaml

from pydantic import BaseModel, ConfigDict

from app.triggers import natural_language as nl
from evals.schema import TriggerCase, TriggerFlavor, TriggerWikiDoc

log = logging.getLogger(__name__)


def load_cases(directory: Path) -> list[TriggerCase]:
    """Load all ``.yaml`` cases under ``directory`` (one case per file)."""
    cases: list[TriggerCase] = []
    for path in sorted(directory.glob("*.yaml")):
        with path.open() as fh:
            raw = yaml.safe_load(fh)
        cases.append(TriggerCase.model_validate(raw))
    if not cases:
        raise ValueError("no trigger cases found in %s" % directory)
    return cases


def _build_wiki_snapshot(docs: list[TriggerWikiDoc]) -> str:
    """Mirror ``app.triggers.diff.build_wiki_snapshot`` shape for the harness."""
    chunks: list[str] = ["=== WIKI (latest version) ==="]
    for d in docs:
        chunks.append("--- %s\n%s\n" % (d.path, d.body.rstrip()))
    return "\n".join(chunks)


def _require(case: TriggerCase, field: str, value: object) -> str:
    """Fail loudly when a flavor-required field is missing.

    The repo convention is "fail loudly, not silent fallbacks" — a
    malformed YAML case must blow up at payload-build time so the
    eval doesn't quietly run on garbage and produce a confident-looking
    bogus score.
    """
    if value is None or value == "":
        raise ValueError(
            "trigger case %s (flavor=%s) missing required field %r"
            % (case.id, case.flavor.value, field)
        )
    return str(value)


def _build_change_view(case: TriggerCase) -> str:
    """Mirror ``app.triggers.diff.build_change_view`` for the harness."""
    path = _require(case, "change_path", case.change_path)
    kind = _require(case, "change_kind", case.change_kind)
    after = _require(case, "after", case.after)
    # ``before`` is allowed to be empty for ``create`` kind — that's how the
    # production builder distinguishes a create from an edit.
    before = case.before or ""
    header = "=== CHANGE ===\nPath: %s\nKind: %s\n" % (path, kind)
    if kind == "create" or not before:
        return "%s\n(new file — full body)\n%s\n" % (header, after.rstrip())
    diff = "".join(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile="before",
            tofile="after",
            n=2,
        )
    )
    return "%s\n<unified diff>\n%s\n</unified diff>\n" % (header, diff.rstrip())


def _build_schedule_block(case: TriggerCase) -> str:
    scope = _require(case, "scope_path", case.scope_path)
    when = _require(case, "when_iso", case.when_iso)
    return "=== SCHEDULED CHECK ===\nScope: %s\nTime: %s\n" % (scope, when)


def _build_new_file_block(case: TriggerCase) -> str:
    path = _require(case, "new_file_path", case.new_file_path)
    body = _require(case, "new_file_body", case.new_file_body)
    return "=== NEW FILE ===\nPath: %s\n\n%s\n" % (path, body.rstrip())


def build_payload(case: TriggerCase) -> str:
    """Compose the payload the natural-language evaluator will see."""
    snapshot = _build_wiki_snapshot(list(case.wiki_state))
    if case.flavor is TriggerFlavor.DELTA:
        return "%s\n\n%s" % (snapshot, _build_change_view(case))
    if case.flavor is TriggerFlavor.SCHEDULE:
        return "%s\n\n%s" % (snapshot, _build_schedule_block(case))
    return "%s\n\n%s" % (snapshot, _build_new_file_block(case))


class TriggerRunResult(BaseModel):
    """Outcome of running one case through the harness.

    Holds the actual matched bool, the rendered message (empty when not
    matched or the flavor has no render phase), and the model-emitted
    reason from phase 1 — all three are inputs to the scorer pass.
    """

    model_config = ConfigDict(frozen=True)

    matched: bool
    reason: str
    message: str


def run_case(case: TriggerCase) -> TriggerRunResult:
    """Drive one case end-to-end through the live natural_language module.

    Phase 2 is skipped when phase 1 says no_match — production never
    renders for a non-firing trigger, so the eval doesn't either.
    """
    payload = build_payload(case)
    if case.flavor is TriggerFlavor.NEW_FILE:
        out = nl.evaluate_new_file_in_dir(case.nl_description, case.message_instruction, payload)
        return TriggerRunResult(matched=out.triggered, reason="", message=out.message)
    if case.flavor is TriggerFlavor.DELTA:
        match = nl.matches(case.nl_description, payload)
        message = ""
        if match.matched and case.message_instruction:
            message = nl.render_message(case.message_instruction, payload, reason=match.reason)
        return TriggerRunResult(matched=match.matched, reason=match.reason, message=message)
    # schedule
    match = nl.matches_snapshot(case.nl_description, payload)
    message = ""
    if match.matched and case.message_instruction:
        message = nl.render_snapshot_message(case.message_instruction, payload, reason=match.reason)
    return TriggerRunResult(matched=match.matched, reason=match.reason, message=message)
