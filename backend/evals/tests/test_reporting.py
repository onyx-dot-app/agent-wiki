"""Unit tests for ``evals.reporting``.

Covers JSONL IO, bootstrap CI math, trigger confusion-matrix metrics,
markdown summary rendering, GitHub-step-summary writer.
"""

# pyright: reportPrivateUsage=false

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from evals import reporting
from evals.schema import CaseResult, MergeConflictCase, ScorerOutcome


def _make_result(
    *,
    case_id: str,
    model: str,
    expected: str = "CHANGE",
    actual: str = "CHANGE",
    scorers: list[ScorerOutcome] | None = None,
    run_index: int = 0,
    error: str = "",
) -> CaseResult:
    return CaseResult(
        case_id=case_id,
        surface="process_instruction",
        provider="anthropic",
        model=model,
        run_index=run_index,
        expected_class=expected,
        actual_class=actual,
        raw_output="",
        scorers=scorers or [],
        error=error,
    )


def test_write_jsonl_round_trip(tmp_path: Path) -> None:
    rows = [
        _make_result(case_id="c1", model="claude-sonnet-4-6"),
        _make_result(case_id="c2", model="claude-sonnet-4-6"),
    ]
    out = tmp_path / "r.jsonl"
    count = reporting.write_jsonl(out, rows)
    assert count == 2
    lines = out.read_text().splitlines()
    assert len(lines) == 2
    reread = [CaseResult.model_validate_json(line) for line in lines]
    assert [r.case_id for r in reread] == ["c1", "c2"]


def test_paired_bootstrap_ci_deterministic() -> None:
    scores = [0.5, 0.6, 0.7, 0.8, 0.9]
    lo1, hi1 = reporting._paired_bootstrap_ci(scores, iterations=200, seed=7)
    lo2, hi2 = reporting._paired_bootstrap_ci(scores, iterations=200, seed=7)
    assert (lo1, hi1) == (lo2, hi2)
    assert lo1 <= sum(scores) / len(scores) <= hi1


def test_paired_bootstrap_ci_empty() -> None:
    assert reporting._paired_bootstrap_ci([]) == (0.0, 0.0)


def test_case_level_scores_averages_runs() -> None:
    rows = [
        _make_result(
            case_id="c1",
            model="m",
            scorers=[ScorerOutcome(name="x", score=0.0, passed=False)],
        ),
        _make_result(
            case_id="c1",
            model="m",
            run_index=1,
            scorers=[ScorerOutcome(name="x", score=1.0, passed=True)],
        ),
    ]
    out = reporting._case_level_scores(rows, "x")
    assert out == [0.5]  # one case, averaged across two runs


def test_trigger_class_metrics_perfect() -> None:
    rows = [
        _make_result(case_id="a", model="m", expected="CHANGE", actual="CHANGE"),
        _make_result(case_id="b", model="m", expected="NO_CHANGE", actual="NO_CHANGE"),
    ]
    summaries = reporting._trigger_class_metrics(rows)
    by_name = {s.name: s.mean for s in summaries}
    assert by_name["trigger.precision[CHANGE]"] == 1.0
    assert by_name["trigger.recall[NO_CHANGE]"] == 1.0
    assert by_name["trigger.macro_f1"] == 1.0


def test_trigger_class_metrics_confusion() -> None:
    rows = [
        # CHANGE → CHANGE (TP)
        _make_result(case_id="a", model="m", expected="CHANGE", actual="CHANGE"),
        # NO_CHANGE → CHANGE (false action)
        _make_result(case_id="b", model="m", expected="NO_CHANGE", actual="CHANGE"),
    ]
    summaries = reporting._trigger_class_metrics(rows)
    by_name = {s.name: s.mean for s in summaries}
    # CHANGE: 1 TP, 1 FP, 0 FN → precision 0.5, recall 1.0
    assert by_name["trigger.precision[CHANGE]"] == 0.5
    assert by_name["trigger.recall[CHANGE]"] == 1.0
    # NO_CHANGE: 0 TP, 0 FP, 1 FN → precision default 1.0, recall 0.0
    assert by_name["trigger.recall[NO_CHANGE]"] == 0.0


def test_summarize_emits_trigger_metrics_when_class_match_present() -> None:
    score = ScorerOutcome(name="trigger_class_match", score=1.0, passed=True)
    rows = [
        _make_result(case_id="a", model="m", expected="CHANGE", actual="CHANGE", scorers=[score]),
    ]
    summary = reporting.summarize(rows, surface="process_instruction")
    names = {s.name for s in summary.per_model["m"]}
    assert "trigger.macro_f1" in names
    assert "trigger_class_match" in names


def test_summarize_skips_trigger_metrics_when_no_class_match() -> None:
    score = ScorerOutcome(name="precision", score=1.0, passed=True)
    rows = [
        _make_result(case_id="a", model="m", scorers=[score]),
    ]
    summary = reporting.summarize(rows, surface="ingest_selector")
    names = {s.name for s in summary.per_model["m"]}
    assert all(not n.startswith("trigger.") for n in names)


def test_print_summary_renders_markdown_table() -> None:
    rows = [
        _make_result(
            case_id="a", model="m", scorers=[ScorerOutcome(name="x", score=0.5, passed=False)]
        ),
    ]
    summary = reporting.summarize(rows, surface="process_instruction")
    buf = io.StringIO()
    reporting.print_summary(summary, stream=buf)
    text = buf.getvalue()
    assert "surface=process_instruction" in text
    assert "| model |" in text
    assert "| m |" in text


def test_write_github_summary_writes_when_env_set(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    rows = [
        _make_result(
            case_id="a", model="m", scorers=[ScorerOutcome(name="x", score=0.5, passed=False)]
        ),
    ]
    summary = reporting.summarize(rows, surface="process_instruction")
    target = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(target))
    reporting.write_github_summary(summary, braintrust_url="https://example.com/exp/foo")
    body = target.read_text()
    assert "## Eval: process_instruction" in body
    assert "Braintrust experiment" in body
    assert "https://example.com/exp/foo" in body
    assert "| m |" in body


def test_write_github_summary_noop_without_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    del tmp_path
    rows = [_make_result(case_id="a", model="m")]
    summary = reporting.summarize(rows, surface="process_instruction")
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    # Should not raise + should not write anything anywhere.
    reporting.write_github_summary(summary)


def test_push_to_braintrust_skips_without_creds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("BRAINTRUST_API_KEY", raising=False)
    monkeypatch.delenv("BRAINTRUST_PROJECT", raising=False)
    url = reporting.push_to_braintrust("exp-1", [_make_result(case_id="a", model="m")])
    assert url == ""


def test_push_merge_conflict_dataset_skips_without_creds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("BRAINTRUST_API_KEY", raising=False)
    monkeypatch.delenv("BRAINTRUST_PROJECT", raising=False)
    case = MergeConflictCase(
        id="mc-x",
        wiki_path="p.md",
        base_body="base",
        current_body="cur",
        draft_body="draft",
    )
    assert reporting.push_merge_conflict_dataset("merge-conflict-update", [case]) == 0


def test_summarize_error_rate_counts_failed_cases() -> None:
    rows = [
        _make_result(case_id="ok", model="m"),
        _make_result(case_id="err", model="m", error="boom"),
    ]
    summary = reporting.summarize(rows, surface="process_instruction")
    error_rate = next(s for s in summary.per_model["m"] if s.name == "error_rate")
    assert error_rate.mean == 0.5


def test_jsonl_preserves_metadata_fields(tmp_path: Path) -> None:
    row = CaseResult(
        case_id="c",
        surface="external_agent",
        provider="anthropic",
        model="m",
        expected_class="x",
        actual_class="x",
        raw_output="",
        scorers=[],
        eval_run_id="abc",
        run_timestamp="2026-05-22T00:00:00Z",
        harness_git_sha="deadbeef",
        dataset_git_sha="cafef00d",
        judge_models=["j1", "j2"],
    )
    out = tmp_path / "r.jsonl"
    reporting.write_jsonl(out, [row])
    reread = json.loads(out.read_text().strip())
    assert reread["eval_run_id"] == "abc"
    assert reread["harness_git_sha"] == "deadbeef"
    assert reread["judge_models"] == ["j1", "j2"]
