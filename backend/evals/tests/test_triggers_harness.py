"""Unit coverage for the trigger eval harness.

Exercises:
* Case YAML loading + schema validation
* Payload composition shape (snapshot + change/schedule/new-file block)
* End-to-end ``run_case`` under the dry-run stub
* Scorer wiring (decision + no-false-fire + reason / message judges short-circuit
  when there's no message)
"""

from __future__ import annotations

from pathlib import Path

import pytest

from evals.schema import FactClaim, TriggerCase, TriggerFlavor, TriggerWikiDoc
from evals.triggers._stub import stub_triggers
from evals.triggers.harness import TriggerRunResult, build_payload, load_cases, run_case
from evals.triggers.run import _score_case  # pyright: ignore[reportPrivateUsage]


CASES_DIR = Path(__file__).resolve().parent.parent / "datasets" / "triggers" / "cases"


def test_load_seed_cases_validate() -> None:
    cases = load_cases(CASES_DIR)
    assert len(cases) >= 10
    ids = {c.id for c in cases}
    assert len(ids) == len(cases), "case ids must be unique"
    flavors = {c.flavor for c in cases}
    # All three flavors covered by the seed set
    assert flavors == {
        TriggerFlavor.DELTA,
        TriggerFlavor.SCHEDULE,
        TriggerFlavor.NEW_FILE,
    }
    # At least one fire and one no-fire per flavor → keeps the WHEN
    # axis honest on regression.
    for flavor in flavors:
        flavor_cases = [c for c in cases if c.flavor is flavor]
        assert any(c.expected_matched for c in flavor_cases), (
            "flavor %s has no positive case" % flavor
        )
        assert any(not c.expected_matched for c in flavor_cases), (
            "flavor %s has no negative case" % flavor
        )


def _make_delta_case(*, matched: bool) -> TriggerCase:
    return TriggerCase(
        id="t-delta-%s" % matched,
        flavor=TriggerFlavor.DELTA,
        nl_description="when status flips to yellow",
        message_instruction="Note the service.",
        wiki_state=[TriggerWikiDoc(path="ops/status.md", body="# Service Status")],
        change_path="ops/status.md",
        change_kind="edit",
        before="api: green",
        after="api: yellow",
        expected_matched=matched,
        expected_message_facts_present=[FactClaim(id="m-api", text="api is mentioned")],
    )


def _make_schedule_case(*, matched: bool) -> TriggerCase:
    return TriggerCase(
        id="t-sched-%s" % matched,
        flavor=TriggerFlavor.SCHEDULE,
        nl_description="when any service is yellow",
        message_instruction="List non-green services.",
        wiki_state=[TriggerWikiDoc(path="ops/status.md", body="api: yellow")],
        scope_path="ops/status.md",
        when_iso="2026-05-26T15:00:00Z",
        expected_matched=matched,
    )


def _make_new_file_case(*, matched: bool) -> TriggerCase:
    return TriggerCase(
        id="t-newfile-%s" % matched,
        flavor=TriggerFlavor.NEW_FILE,
        nl_description="when a new postmortem is added",
        message_instruction="Link the postmortem.",
        wiki_state=[TriggerWikiDoc(path="incidents/index.md", body="# Incidents")],
        new_file_path="incidents/2026-05-26-x.md",
        new_file_body="# 2026-05-26 incident",
        expected_matched=matched,
    )


@pytest.mark.parametrize(
    "flavor", [TriggerFlavor.DELTA, TriggerFlavor.SCHEDULE, TriggerFlavor.NEW_FILE]
)
def test_build_payload_includes_snapshot_header(flavor: TriggerFlavor) -> None:
    if flavor is TriggerFlavor.DELTA:
        case = _make_delta_case(matched=True)
    elif flavor is TriggerFlavor.SCHEDULE:
        case = _make_schedule_case(matched=True)
    else:
        case = _make_new_file_case(matched=True)
    payload = build_payload(case)
    assert payload.startswith("=== WIKI (latest version) ===")
    if flavor is TriggerFlavor.DELTA:
        assert "=== CHANGE ===" in payload
    elif flavor is TriggerFlavor.SCHEDULE:
        assert "=== SCHEDULED CHECK ===" in payload
    else:
        assert "=== NEW FILE ===" in payload


@pytest.mark.parametrize("matched", [True, False])
def test_run_case_delta_via_stub(matched: bool) -> None:
    case = _make_delta_case(matched=matched)
    with stub_triggers([case]):
        out = run_case(case)
    assert out.matched is matched
    if matched:
        assert out.message  # phase-2 ran
    else:
        assert out.message == ""  # phase-2 skipped


@pytest.mark.parametrize("matched", [True, False])
def test_run_case_schedule_via_stub(matched: bool) -> None:
    case = _make_schedule_case(matched=matched)
    with stub_triggers([case]):
        out = run_case(case)
    assert out.matched is matched
    if matched:
        assert out.message
    else:
        assert out.message == ""


@pytest.mark.parametrize("matched", [True, False])
def test_run_case_new_file_via_stub(matched: bool) -> None:
    case = _make_new_file_case(matched=matched)
    with stub_triggers([case]):
        out = run_case(case)
    assert out.matched is matched
    if matched:
        assert out.message
    else:
        assert out.message == ""


def test_score_case_decision_only_when_no_judge_facts() -> None:
    """A case without labeled facts should still produce decision + no-false-fire
    rows without invoking the LLM judge (so dry-run smoke stays offline)."""
    case = _make_delta_case(matched=False)
    with stub_triggers([case]):
        out = run_case(case)
    rows = _score_case(case, out, judge_models=None)
    names = {r.name for r in rows}
    assert "trigger_match_decision" in names
    assert "no_false_fire_compliance" in names
    # No reason facts on the negative case → reason scorer short-circuits.
    assert {r.name for r in rows if r.name.startswith("reason_")} == {"reason_facts_present"}


def test_build_payload_fails_loudly_on_missing_required_field() -> None:
    """A delta case with no change_path must raise, not silently emit Path: ''."""
    case = TriggerCase(
        id="t-bad-delta",
        flavor=TriggerFlavor.DELTA,
        nl_description="whatever",
        wiki_state=[TriggerWikiDoc(path="x.md", body="hi")],
        change_path=None,
        change_kind="edit",
        after="hi there",
        expected_matched=True,
    )
    with pytest.raises(ValueError, match="change_path"):
        build_payload(case)


def test_score_case_match_axis_records_false_fire() -> None:
    """When ground truth says no_fire but model fired, no_false_fire = 0.0."""
    case = _make_delta_case(matched=False)
    fake = TriggerRunResult(matched=True, reason="stub said yes anyway", message="STUB")
    rows = _score_case(case, fake, judge_models=None)
    by_name = {r.name: r for r in rows}
    assert by_name["trigger_match_decision"].score == 0.0
    assert by_name["no_false_fire_compliance"].score == 0.0
