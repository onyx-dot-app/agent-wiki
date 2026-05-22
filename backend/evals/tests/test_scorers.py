"""Unit tests for ``evals.scorers``.

Pure-function coverage for trigger / bloat / markdown / entity-density /
diff-addition / selector-set scorers. The judge panel uses the same
``app.llm.client.complete`` stub trick the runners do — patch the
module-level callable to return canned ``CompletionResult``s.
"""

from __future__ import annotations

from collections.abc import Generator

import pytest

from app.llm import client as llm_client
from app.llm.client import CompletionResult

from evals import scorers
from evals.schema import FactClaim, TriggerClass


def test_trigger_class_match_exact() -> None:
    out = scorers.trigger_class_match(TriggerClass.CHANGE, TriggerClass.CHANGE)
    assert out.score == 1.0
    assert out.passed


def test_trigger_class_match_mismatch() -> None:
    out = scorers.trigger_class_match(TriggerClass.CHANGE, TriggerClass.NO_CHANGE)
    assert out.score == 0.0
    assert not out.passed


def test_bloat_ratio_within_budget() -> None:
    out = scorers.bloat_ratio("a" * 100, "b" * 150, max_ratio=2.0)
    assert out.score == 1.0
    assert out.passed


def test_bloat_ratio_overshoot_partial_credit() -> None:
    out = scorers.bloat_ratio("a" * 100, "b" * 300, max_ratio=2.0)
    assert 0.0 < out.score < 1.0
    assert not out.passed


def test_bloat_ratio_empty_base() -> None:
    out = scorers.bloat_ratio("", "anything")
    assert out.score == 1.0
    assert out.passed


def test_markdown_valid_clean() -> None:
    out = scorers.markdown_valid("# H1\n\n## H2\n\nbody text")
    assert out.passed


def test_markdown_valid_empty() -> None:
    out = scorers.markdown_valid("   \n  ")
    assert not out.passed


def test_markdown_valid_heading_skip() -> None:
    out = scorers.markdown_valid("# H1\n\n### H3 skipped h2\n")
    assert not out.passed
    assert "jumped" in out.detail


def test_markdown_valid_with_table_ok() -> None:
    # markdown-it pads ragged tables to header width, so a column-drift
    # check fires only on tables the parser flags itself; for now this
    # guards that well-formed tables don't false-positive.
    body = "| col1 | col2 |\n| -- | -- |\n| 1 | 2 |\n| 3 | 4 |\n"
    out = scorers.markdown_valid(body)
    assert out.passed


def test_entity_density_delta_stable() -> None:
    body = "The Auth Service runs on port 8080. See `auth.py` for details. v1.2.3."
    out = scorers.entity_density_delta(body, body)
    assert out.score == 1.0


def test_entity_density_delta_large_drop() -> None:
    rich = "Auth Service v1.2.3 with `endpoint` runs on 8080ms. " * 5
    sparse = "things happen sometimes maybe. " * 5
    out = scorers.entity_density_delta(rich, sparse)
    assert out.score < 0.5


def test_diff_addition_ratio_no_changes() -> None:
    body = "the quick brown fox jumps"
    out = scorers.diff_addition_ratio(body, body)
    assert out.score == 1.0
    assert out.passed


def test_diff_addition_ratio_heavy_rewrite() -> None:
    cur = "the quick brown fox"
    new = "the quick brown fox " + "extra " * 20
    out = scorers.diff_addition_ratio(cur, new)
    assert out.score < 1.0


def test_selector_set_metrics_exact() -> None:
    p, r, f1 = scorers.selector_set_metrics(["a", "b", "c"], ["a", "b", "c"])
    assert p.score == 1.0
    assert r.score == 1.0
    assert f1.score == 1.0


def test_selector_set_metrics_false_positive() -> None:
    p, r, f1 = scorers.selector_set_metrics(["a"], ["a", "b"])
    assert p.score == 0.5
    assert r.score == 1.0
    assert 0.0 < f1.score < 1.0


def test_selector_set_metrics_false_negative() -> None:
    p, r, f1 = scorers.selector_set_metrics(["a", "b"], ["a"])
    assert p.score == 1.0
    assert r.score == 0.5
    assert 0.0 < f1.score < 1.0


def test_selector_set_metrics_both_empty() -> None:
    p, r, f1 = scorers.selector_set_metrics([], [])
    # No expected, no actual = perfect on both axes by convention.
    assert p.score == 1.0
    assert r.score == 1.0


# --------------------------------------------------------------------------- #
# Judge panel — stub ``client.complete`` to drive verdicts deterministically  #
# --------------------------------------------------------------------------- #


@pytest.fixture
def stub_judge() -> Generator[dict[str, list[str]], None, None]:
    """Patch ``client.complete`` with a queue-per-model verdict string.

    Each call pops the next verdict line for whichever model is requested.
    Tests append per-model queues to set up panel scenarios.
    """
    queues: dict[str, list[str]] = {}
    original = llm_client.complete

    def _stub(messages, *, model=None, tools=None, max_tokens=llm_client.DEFAULT_MAX_TOKENS):  # type: ignore[no-untyped-def]
        del messages, tools, max_tokens
        q = queues.get(model or "")
        if not q:
            return CompletionResult(text="VERDICT: UNKNOWN | RATIONALE: queue empty")
        return CompletionResult(text=q.pop(0))

    llm_client.complete = _stub  # type: ignore[assignment]
    try:
        yield queues
    finally:
        llm_client.complete = original  # type: ignore[assignment]


def test_judge_panel_majority_yes(stub_judge: dict[str, list[str]]) -> None:
    stub_judge["claude-haiku-4-5"] = ["VERDICT: YES | RATIONALE: stated explicitly"]
    stub_judge["gpt-5-mini"] = ["VERDICT: YES | RATIONALE: matches body"]
    out = scorers.facts_present(
        "body",
        [FactClaim(id="f1", text="some fact")],
        judge_models=("claude-haiku-4-5", "gpt-5-mini"),
    )
    assert out.score == 1.0
    assert out.passed


def test_judge_panel_majority_no(stub_judge: dict[str, list[str]]) -> None:
    stub_judge["claude-haiku-4-5"] = ["VERDICT: NO | RATIONALE: absent"]
    stub_judge["gpt-5-mini"] = ["VERDICT: NO | RATIONALE: absent"]
    out = scorers.facts_present(
        "body",
        [FactClaim(id="f1", text="not in body")],
        judge_models=("claude-haiku-4-5", "gpt-5-mini"),
    )
    assert out.score == 0.0
    assert not out.passed


def test_judge_panel_tie_is_false(stub_judge: dict[str, list[str]]) -> None:
    """1 YES, 1 NO → tie → False (conservative)."""
    stub_judge["claude-haiku-4-5"] = ["VERDICT: YES | RATIONALE: maybe"]
    stub_judge["gpt-5-mini"] = ["VERDICT: NO | RATIONALE: nope"]
    out = scorers.facts_present(
        "body",
        [FactClaim(id="f1", text="contested")],
        judge_models=("claude-haiku-4-5", "gpt-5-mini"),
    )
    assert out.score == 0.0


def test_judge_panel_unknown_abstains(stub_judge: dict[str, list[str]]) -> None:
    """UNKNOWN abstains; remaining YES carries."""
    stub_judge["claude-haiku-4-5"] = ["VERDICT: YES | RATIONALE: clear"]
    stub_judge["gpt-5-mini"] = ["VERDICT: UNKNOWN | RATIONALE: ambiguous"]
    out = scorers.facts_present(
        "body",
        [FactClaim(id="f1", text="x")],
        judge_models=("claude-haiku-4-5", "gpt-5-mini"),
    )
    assert out.score == 1.0


def test_judge_panel_no_claims_returns_perfect() -> None:
    out = scorers.facts_present("body", [])
    assert out.score == 1.0
    assert out.passed


def test_judge_panel_error_treated_as_abstain(stub_judge: dict[str, list[str]]) -> None:
    """Caller-side judge failure (returned via error verdict) does not crash the run."""
    stub_judge["claude-haiku-4-5"] = ["VERDICT: NO | RATIONALE: missing"]
    stub_judge["gpt-5-mini"] = []  # empty queue → returns UNKNOWN
    out = scorers.facts_present(
        "body",
        [FactClaim(id="f1", text="x")],
        judge_models=("claude-haiku-4-5", "gpt-5-mini"),
    )
    # NO + UNKNOWN-abstain → False (no YES wins)
    assert out.score == 0.0
