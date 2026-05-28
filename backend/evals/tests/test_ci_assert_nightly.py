"""Unit tests for the nightly regression-floor asserter.

Builds synthetic ``CaseResult`` JSONL files and checks the asserter
flags below-floor scores per (surface, scorer, model) while passing
on the at-or-above case. Also covers the file-skip + multi-surface
(wiki_updater splits into process_instruction + reconcile_document)
paths.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from evals.ci_assert_nightly import check_run_file, main
from evals.schema import CaseResult, ScorerOutcome


def _row(
    *,
    surface: str,
    model: str,
    case_id: str,
    scorer: str,
    score: float,
    expected: str = "X",
    actual: str = "X",
) -> str:
    r = CaseResult(
        case_id=case_id,
        surface=surface,  # pyright: ignore[reportArgumentType]
        provider="anthropic",
        model=model,
        expected_class=expected,
        actual_class=actual,
        raw_output="{}",
        scorers=[ScorerOutcome(name=scorer, score=score, passed=score >= 0.5)],
    )
    return r.model_dump_json()


def _write_run(tmp_path: Path, name: str, lines: list[str]) -> Path:
    p = tmp_path / name
    p.write_text("\n".join(lines) + "\n")
    return p


def test_passes_when_all_scores_above_floor(tmp_path: Path) -> None:
    p = _write_run(
        tmp_path,
        "nightly_external_agent.jsonl",
        [
            _row(
                surface="external_agent",
                model="claude-sonnet-4-6",
                case_id="c1",
                scorer="facts_preserved_avg",
                score=0.95,
            ),
            _row(
                surface="external_agent",
                model="claude-sonnet-4-6",
                case_id="c2",
                scorer="facts_preserved_avg",
                score=0.90,
            ),
        ],
    )
    assert check_run_file(p) == []


def test_fails_when_below_floor(tmp_path: Path) -> None:
    p = _write_run(
        tmp_path,
        "nightly_external_agent.jsonl",
        [
            _row(
                surface="external_agent",
                model="gpt-5",
                case_id="c1",
                scorer="facts_preserved_avg",
                score=0.50,
            ),
        ],
    )
    errs = check_run_file(p)
    assert errs, "expected a failure"
    assert "facts_preserved_avg" in errs[0]
    assert "gpt-5" in errs[0]
    assert "external_agent" in errs[0]


def test_wiki_updater_splits_into_two_surfaces(tmp_path: Path) -> None:
    """One file, two distinct sub-surfaces — each threshold-checked separately."""
    p = _write_run(
        tmp_path,
        "nightly_wiki_updater.jsonl",
        [
            # process_instruction case passes its floor (0.80)
            _row(
                surface="process_instruction",
                model="claude-sonnet-4-6",
                case_id="pi-01",
                scorer="trigger_class_match",
                score=1.0,
            ),
            # reconcile_document case dips below its floor (0.45)
            _row(
                surface="reconcile_document",
                model="claude-sonnet-4-6",
                case_id="rd-01",
                scorer="trigger_class_match",
                score=0.30,
            ),
            _row(
                surface="reconcile_document",
                model="claude-sonnet-4-6",
                case_id="rd-02",
                scorer="trigger_class_match",
                score=0.30,
            ),
        ],
    )
    errs = check_run_file(p)
    assert any("reconcile_document" in e for e in errs)
    assert not any("process_instruction" in e for e in errs)


def test_unknown_filename_skipped(tmp_path: Path) -> None:
    """A rogue jsonl file under runs/ should not fail the asserter."""
    p = _write_run(
        tmp_path,
        "ad-hoc-debug-run.jsonl",
        [
            _row(
                surface="external_agent",
                model="claude-sonnet-4-6",
                case_id="c1",
                scorer="facts_preserved_avg",
                score=0.10,
            ),
        ],
    )
    assert check_run_file(p) == []


def test_missing_or_empty_file_fails(tmp_path: Path) -> None:
    missing = tmp_path / "nightly_external_agent.jsonl"
    errs = check_run_file(missing)
    assert errs
    assert "missing or empty" in errs[0]
    empty = tmp_path / "nightly_triggers.jsonl"
    empty.write_text("")
    assert any("missing or empty" in e or "zero rows" in e for e in check_run_file(empty))


def test_main_fails_when_expected_file_missing(tmp_path: Path) -> None:
    """A nightly step that exits 0 but never writes its file must trip the gate."""
    # Write the other expected files so only one is missing.
    for name in (
        "nightly_wiki_updater.jsonl",
        "nightly_ingest_selector.jsonl",
        "nightly_external_agent.jsonl",
    ):
        _write_run(
            tmp_path,
            name,
            [
                _row(
                    surface="external_agent",
                    model="claude-sonnet-4-6",
                    case_id="c1",
                    scorer="facts_preserved_avg",
                    score=0.95,
                )
            ],
        )
    # nightly_triggers.jsonl intentionally not written
    rc = main(["ci_assert_nightly", str(tmp_path)])
    assert rc == 1, "expected non-zero exit when an expected file is missing"


@pytest.mark.parametrize(
    "scorer,score,should_fail",
    [
        ("facts_preserved_avg", 0.79, True),
        ("facts_preserved_avg", 0.81, False),
        ("update_f1", 0.94, True),
        ("update_f1", 0.96, False),
        ("no_touch_compliance", 0.89, True),
        ("no_touch_compliance", 0.91, False),
    ],
)
def test_external_agent_thresholds(
    tmp_path: Path, scorer: str, score: float, should_fail: bool
) -> None:
    p = _write_run(
        tmp_path,
        "nightly_external_agent.jsonl",
        [
            _row(
                surface="external_agent",
                model="claude-sonnet-4-6",
                case_id="c1",
                scorer=scorer,
                score=score,
            )
        ],
    )
    errs = check_run_file(p)
    if should_fail:
        assert errs
    else:
        assert errs == []
