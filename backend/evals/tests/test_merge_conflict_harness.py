"""Unit coverage for the merge_conflict_update eval surface."""

from __future__ import annotations

from pathlib import Path

from evals.merge_conflict_update._stub import stub_merge_conflict
from evals.merge_conflict_update.harness import load_cases, run_case
from evals.merge_conflict_update.run import _score_case  # pyright: ignore[reportPrivateUsage]
from evals.schema import FactClaim, MergeConflictCase


CASES_DIR = Path(__file__).resolve().parent.parent / "datasets" / "merge_conflict_update" / "cases"


def _make_case(
    *,
    case_id: str = "mc-test",
    base: str = "base body",
    current: str = "current body",
    draft: str = "draft body",
    commit_msg: str | None = None,
    expects_annotation: bool = False,
) -> MergeConflictCase:
    return MergeConflictCase(
        id=case_id,
        wiki_path="test/page.md",
        base_body=base,
        current_body=current,
        draft_body=draft,
        current_commit_message=commit_msg,
        facts_from_current_present=[FactClaim(id="c1", text="current is present")],
        facts_from_draft_present=[FactClaim(id="d1", text="draft is present")],
        facts_must_not_appear=[FactClaim(id="h1", text="something invented")],
        expects_conflict_annotation=expects_annotation,
    )


def test_load_seed_cases_validate() -> None:
    cases = load_cases(CASES_DIR)
    assert len(cases) >= 8
    ids = {c.id for c in cases}
    assert len(ids) == len(cases), "case ids must be unique"
    # Both conflict-annotation modes are exercised
    assert any(c.expects_conflict_annotation for c in cases)
    assert any(not c.expects_conflict_annotation for c in cases)


def test_run_case_via_stub_returns_nonempty_merge() -> None:
    case = _make_case(
        base="# Page\n\nBase paragraph that is unique per case",
        current="# Page\n\nCurrent paragraph",
        draft="# Page\n\nDraft paragraph",
    )
    with stub_merge_conflict([case]):
        merged = run_case(case)
    assert merged, "stub must produce non-empty merged body"
    assert "Draft paragraph" in merged
    assert "Current paragraph" in merged


def test_score_case_records_conflict_annotation_when_expected() -> None:
    case = _make_case(
        base="# Page\n\nBase line unique XYZ",
        current="# Page\n\nCurrent",
        draft="# Page\n\nDraft",
        commit_msg="add new fact",
        expects_annotation=True,
    )
    with stub_merge_conflict([case]):
        merged = run_case(case)
    rows = _score_case(case, merged, judge_models=None)
    names = {r.name for r in rows}
    assert "conflict_annotation_present" in names
    annotation_row = next(r for r in rows if r.name == "conflict_annotation_present")
    # Stub injects the marker when commit_msg is set + annotation expected
    assert annotation_row.score == 1.0


def test_score_case_skips_annotation_when_not_expected() -> None:
    case = _make_case(
        base="# Page\n\nBase ABC unique",
        current="# Page\n\nCurrent only",
        draft="# Page\n\nDraft only",
        expects_annotation=False,
    )
    with stub_merge_conflict([case]):
        merged = run_case(case)
    rows = _score_case(case, merged, judge_models=None)
    names = {r.name for r in rows}
    assert "conflict_annotation_present" not in names


def test_annotation_pattern_does_not_match_bare_prose() -> None:
    """Bare 'migrated from:' / 'inherited from BaseClass' must not be a match."""
    from evals.merge_conflict_update.run import (
        _has_annotation,  # pyright: ignore[reportPrivateUsage]
    )

    assert not _has_annotation("# Notes\n\nMigrated from: v1.\n")
    assert not _has_annotation("class Foo inherited from BaseClass.\n")
    assert not _has_annotation("Output from the reconciler.\n")


def test_annotation_pattern_matches_production_forms() -> None:
    """Match the agent prompt's parenthesised conflict annotation."""
    from evals.merge_conflict_update.run import (
        _has_annotation,  # pyright: ignore[reportPrivateUsage]
    )

    assert _has_annotation("Availability target 99.99% (99.95% from: raise auth target)")
    assert _has_annotation("Latency p95 < 250ms (300ms from another update)")
    assert _has_annotation("body\n<!-- conflict from: some commit -->")


def test_score_case_always_emits_markdown_valid() -> None:
    case = _make_case(
        base="# Page\n\nUNIQUE-DEF",
        current="# Page\n\nC",
        draft="# Page\n\nD",
    )
    with stub_merge_conflict([case]):
        merged = run_case(case)
    rows = _score_case(case, merged, judge_models=None)
    assert any(r.name == "markdown_valid" for r in rows)
