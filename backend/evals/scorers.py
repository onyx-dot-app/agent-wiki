"""Scorers shared across surfaces.

Deterministic scorers: trigger_class_match, bloat_ratio, markdown_valid,
selector_set_metrics. LLM-judge scorers: facts_present, facts_preserved,
each evaluated by a panel of judges with structured rationale + an
``unknown`` escape hatch; per-fact verdict is the panel majority.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Sequence

from app.llm import client

from evals._llm_override import resolve_provider, use_model
from evals.schema import FactClaim, ScorerOutcome, TriggerClass


log = logging.getLogger(__name__)


DEFAULT_JUDGE_PANEL: tuple[str, ...] = (
    "claude-haiku-4-5",
    "gpt-5-mini",
    "gemini-2.5-flash",
)


_JUDGE_SYSTEM = (
    "You are an evaluation judge. The user gives you a wiki page body and one "
    "factual claim. Decide whether the claim is supported by the body. Reason "
    "in one short sentence first, then emit the structured verdict.\n\n"
    "Strictness: a claim that is only partially present or that requires "
    "inference beyond what the body states is NO. If the body is ambiguous "
    "or you cannot tell, emit UNKNOWN — do not guess.\n\n"
    "Respond with ONE LINE in this exact format:\n"
    "VERDICT: <YES|NO|UNKNOWN> | RATIONALE: <one short sentence>"
)


_VERDICT_RE = re.compile(
    r"VERDICT:\s*(YES|NO|UNKNOWN)\s*\|\s*RATIONALE:\s*(.+)", re.IGNORECASE | re.DOTALL
)


def trigger_class_match(expected: TriggerClass, actual: TriggerClass) -> ScorerOutcome:
    ok = expected == actual
    return ScorerOutcome(
        name="trigger_class_match",
        score=1.0 if ok else 0.0,
        passed=ok,
        detail=f"expected={expected.value} actual={actual.value}",
    )


def bloat_ratio(current_body: str, new_body: str, max_ratio: float = 2.0) -> ScorerOutcome:
    if not current_body:
        return ScorerOutcome(name="bloat_ratio", score=1.0, passed=True, detail="empty-base")
    ratio = len(new_body) / len(current_body)
    if ratio <= max_ratio:
        return ScorerOutcome(
            name="bloat_ratio",
            score=1.0,
            passed=True,
            detail=f"ratio={ratio:.2f} max={max_ratio:.2f}",
        )
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
    if not body.strip():
        return ScorerOutcome(name="markdown_valid", score=0.0, passed=False, detail="empty")
    fences = len(_MD_FENCE_RE.findall(body))
    if fences % 2 != 0:
        return ScorerOutcome(
            name="markdown_valid",
            score=0.0,
            passed=False,
            detail=f"unclosed code fence (count={fences})",
        )
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


def _judge_one(body: str, claim: FactClaim, *, judge_model: str) -> tuple[str, str]:
    """Single judge vote. Returns (verdict, rationale). Verdict ∈ {yes,no,unknown,error}."""
    user = f"Wiki page body:\n---\n{body}\n---\n\nClaim: {claim.text}"
    judge_provider = resolve_provider(judge_model)

    def _call() -> "client.CompletionResult":
        return client.complete(
            messages=[
                {"role": "system", "content": _JUDGE_SYSTEM},
                {"role": "user", "content": user},
            ],
            model=judge_model,
            max_tokens=2048,
        )

    try:
        if judge_provider:
            with use_model(judge_provider, judge_model):
                result = _call()
        else:
            result = _call()
        match = _VERDICT_RE.search(result.text)
        if match:
            return (match.group(1).lower(), match.group(2).strip()[:200])
        text = result.text.strip().upper()
        if text.startswith("YES"):
            return ("yes", "")
        if text.startswith("NO"):
            return ("no", "")
        return ("unknown", result.text.strip()[:200])
    except Exception as exc:
        log.warning("judge %s failed for claim %s: %s", judge_model, claim.id, exc)
        return ("error", str(exc)[:200])


def _judge_with_panel(
    body: str, claim: FactClaim, *, judge_models: tuple[str, ...]
) -> tuple[bool, list[dict[str, str]]]:
    """Each judge votes; majority wins. Returns (passed, per_judge_records).

    `unknown` / `error` abstain. Tie → False (conservative — surfaces as a
    regression that's easier to investigate than a silent pass).
    """
    records: list[dict[str, str]] = []
    yes_count = 0
    no_count = 0
    for jm in judge_models:
        verdict, rationale = _judge_one(body, claim, judge_model=jm)
        records.append({"judge": jm, "verdict": verdict, "rationale": rationale})
        if verdict == "yes":
            yes_count += 1
        elif verdict == "no":
            no_count += 1
    return (yes_count > no_count, records)


def _resolve_panel(judge_models: tuple[str, ...] | None) -> tuple[str, ...]:
    return judge_models if judge_models else DEFAULT_JUDGE_PANEL


def facts_present(
    body: str,
    claims: list[FactClaim],
    *,
    judge_models: tuple[str, ...] | None = None,
) -> ScorerOutcome:
    if not claims:
        return ScorerOutcome(name="facts_present", score=1.0, passed=True, detail="no claims")
    panel = _resolve_panel(judge_models)
    verdicts: list[bool] = []
    audit: list[dict[str, list[dict[str, str]] | str]] = []
    for c in claims:
        passed, records = _judge_with_panel(body, c, judge_models=panel)
        verdicts.append(passed)
        audit.append({"claim_id": c.id, "passed": str(passed), "judges": records})
    score = sum(verdicts) / len(verdicts)
    missing = [c.id for c, ok in zip(claims, verdicts, strict=True) if not ok]
    detail = json.dumps({"missing": missing, "panel_audit": audit}, default=str)[:4000]
    return ScorerOutcome(name="facts_present", score=score, passed=score >= 0.8, detail=detail)


def facts_preserved(
    body: str,
    claims: list[FactClaim],
    *,
    judge_models: tuple[str, ...] | None = None,
) -> ScorerOutcome:
    if not claims:
        return ScorerOutcome(name="facts_preserved", score=1.0, passed=True, detail="no claims")
    panel = _resolve_panel(judge_models)
    verdicts: list[bool] = []
    audit: list[dict[str, list[dict[str, str]] | str]] = []
    for c in claims:
        passed, records = _judge_with_panel(body, c, judge_models=panel)
        verdicts.append(passed)
        audit.append({"claim_id": c.id, "passed": str(passed), "judges": records})
    score = sum(verdicts) / len(verdicts)
    lost = [c.id for c, ok in zip(claims, verdicts, strict=True) if not ok]
    detail = json.dumps({"lost": lost, "panel_audit": audit}, default=str)[:4000]
    return ScorerOutcome(name="facts_preserved", score=score, passed=score >= 0.9, detail=detail)
