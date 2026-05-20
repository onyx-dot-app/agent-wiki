"""Scorers shared across surfaces.

Two flavors:

* **Deterministic** — ``trigger_class_match``, ``bloat_ratio``, ``markdown_valid``,
  ``selector_set_metrics``. No LLM call. Cheap. Run every time.
* **LLM-judge** — ``facts_present``, ``facts_preserved``. Each labeled fact is
  scored by a fresh single-shot LLM call asking ``YES`` or ``NO`` for whether
  the body satisfies the claim. The judge goes through ``app.llm.client.complete``
  — same seam as the agent under test — so we keep one provider integration.

Every scorer returns a ``ScorerOutcome`` with a numeric score in [0, 1] (where
1 = best), a boolean ``passed`` derived from a sensible default threshold, and a
short ``detail`` string describing what was measured. Result rows preserve the
raw score so thresholds can change without re-running.
"""

from __future__ import annotations

import logging
import re
from typing import Sequence

from app.llm import client

from evals.schema import FactClaim, ScorerOutcome, TriggerClass


log = logging.getLogger(__name__)


_JUDGE_SYSTEM = (
    "You are an evaluation judge. The user gives you a wiki page body and one "
    "factual claim. Decide whether the claim is supported by the body as written. "
    "Be strict — if the claim is only partially present, answer NO. "
    "Respond with the single word YES or NO and nothing else."
)


def trigger_class_match(expected: TriggerClass, actual: TriggerClass) -> ScorerOutcome:
    """Exact match on the trinary trigger class.

    The whole point of the WHEN axis — a model that ignores correct
    NO_CHANGE or IRRELEVANT cases is as broken as one that misses real
    updates.
    """
    ok = expected == actual
    return ScorerOutcome(
        name="trigger_class_match",
        score=1.0 if ok else 0.0,
        passed=ok,
        detail=f"expected={expected.value} actual={actual.value}",
    )


def bloat_ratio(current_body: str, new_body: str, max_ratio: float = 2.0) -> ScorerOutcome:
    """Flag when the new body is more than ``max_ratio`` times the old.

    Deterministic guardrail against the failure mode CLAUDE.md calls out
    ("the document_updater system prompt forbids bloat ... but we'll need
    eval data"). 1.0 if within budget, scaled penalty as it overshoots.
    """
    if not current_body:
        # Brand new page — ratio doesn't apply
        return ScorerOutcome(name="bloat_ratio", score=1.0, passed=True, detail="empty-base")
    ratio = len(new_body) / len(current_body)
    if ratio <= max_ratio:
        return ScorerOutcome(
            name="bloat_ratio",
            score=1.0,
            passed=True,
            detail=f"ratio={ratio:.2f} max={max_ratio:.2f}",
        )
    # Penalty curve: 1.0 at budget, 0 at 4x budget, clamped.
    overshoot = ratio - max_ratio
    score = max(0.0, 1.0 - overshoot / (max_ratio * 2))
    return ScorerOutcome(
        name="bloat_ratio",
        score=score,
        passed=False,
        detail=f"ratio={ratio:.2f} max={max_ratio:.2f} overshoot={overshoot:.2f}",
    )


_MD_HEADING_LEVEL_RE = re.compile(r"^(#{1,6})\s+\S", re.MULTILINE)
_MD_FENCE_RE = re.compile(r"^```", re.MULTILINE)


def markdown_valid(body: str) -> ScorerOutcome:
    """Cheap structural checks on the new body.

    Catches obvious failures — unclosed fenced code blocks, skipped heading
    levels (a `###` under a `#` with no `##`), trailing junk. Not a full
    parser; we don't want a heavyweight dep for this.
    """
    if not body.strip():
        return ScorerOutcome(name="markdown_valid", score=0.0, passed=False, detail="empty")

    # Even number of code fences
    fences = len(_MD_FENCE_RE.findall(body))
    if fences % 2 != 0:
        return ScorerOutcome(
            name="markdown_valid",
            score=0.0,
            passed=False,
            detail=f"unclosed code fence (count={fences})",
        )

    # Heading hierarchy — no level jumped more than +1 from the previous.
    levels = [len(m.group(1)) for m in _MD_HEADING_LEVEL_RE.finditer(body)]
    prev = 0
    for level in levels:
        if prev and level > prev + 1:
            return ScorerOutcome(
                name="markdown_valid",
                score=0.5,
                passed=False,
                detail=f"heading jumped from h{prev} to h{level}",
            )
        prev = level

    return ScorerOutcome(name="markdown_valid", score=1.0, passed=True, detail="ok")


def selector_set_metrics(
    expected: Sequence[str],
    actual: Sequence[str],
) -> tuple[ScorerOutcome, ScorerOutcome, ScorerOutcome]:
    """Precision / recall / F1 over the kept-paths set.

    For the ingest selector: ``expected`` is the labeled relevant set,
    ``actual`` is what the selector kept. Returns three ``ScorerOutcome``s.
    """
    expected_set = set(expected)
    actual_set = set(actual)
    tp = len(expected_set & actual_set)
    fp = len(actual_set - expected_set)
    fn = len(expected_set - actual_set)
    precision = tp / (tp + fp) if (tp + fp) else 1.0
    recall = tp / (tp + fn) if (tp + fn) else 1.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    detail = f"tp={tp} fp={fp} fn={fn}"
    return (
        ScorerOutcome(name="precision", score=precision, passed=precision >= 0.8, detail=detail),
        ScorerOutcome(name="recall", score=recall, passed=recall >= 0.8, detail=detail),
        ScorerOutcome(name="f1", score=f1, passed=f1 >= 0.8, detail=detail),
    )


def _judge_one_fact(body: str, claim: FactClaim, *, judge_model: str | None) -> bool:
    """Ask the configured LLM whether ``body`` supports ``claim``.

    Returns ``False`` on any error so a flaky judge biases toward marking a
    case as not-supported (which surfaces as a regression — easier to spot
    than the opposite).
    """
    user = (
        "Wiki page body:\n---\n"
        f"{body}\n"
        "---\n\n"
        f"Claim: {claim.text}\n\n"
        "Is the claim supported by the body? Answer YES or NO."
    )
    try:
        result = client.complete(
            messages=[
                {"role": "system", "content": _JUDGE_SYSTEM},
                {"role": "user", "content": user},
            ],
            model=judge_model,
            max_tokens=8,
        )
        verdict = result.text.strip().upper()
        # Tolerant parse: accept "YES.", "YES — because", but require the first token.
        return verdict.startswith("YES")
    except Exception:
        log.warning("judge call failed for claim %s", claim.id, exc_info=True)
        return False


def facts_present(
    body: str,
    claims: list[FactClaim],
    *,
    judge_model: str | None = None,
) -> ScorerOutcome:
    """Fraction of ``claims`` the judge says are present in ``body``.

    Empty claim list → vacuously 1.0 (no facts to check).
    """
    if not claims:
        return ScorerOutcome(name="facts_present", score=1.0, passed=True, detail="no claims")
    verdicts = [_judge_one_fact(body, c, judge_model=judge_model) for c in claims]
    score = sum(verdicts) / len(verdicts)
    missing = [c.id for c, ok in zip(claims, verdicts, strict=True) if not ok]
    return ScorerOutcome(
        name="facts_present",
        score=score,
        passed=score >= 0.8,
        detail=f"missing={missing}" if missing else "all present",
    )


def facts_preserved(
    body: str,
    claims: list[FactClaim],
    *,
    judge_model: str | None = None,
) -> ScorerOutcome:
    """Fraction of must-keep ``claims`` the judge says still appear.

    Same shape as ``facts_present`` but the semantic is different: each
    claim is something the page had BEFORE the update and must STILL have
    after. Missing facts here are the "information loss" failure mode.
    """
    if not claims:
        return ScorerOutcome(name="facts_preserved", score=1.0, passed=True, detail="no claims")
    verdicts = [_judge_one_fact(body, c, judge_model=judge_model) for c in claims]
    score = sum(verdicts) / len(verdicts)
    lost = [c.id for c, ok in zip(claims, verdicts, strict=True) if not ok]
    return ScorerOutcome(
        name="facts_preserved",
        score=score,
        passed=score >= 0.9,
        detail=f"lost={lost}" if lost else "all preserved",
    )
