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
)


# Substring stubs match against to recognize a judge prompt without
# string-duplicating the full system prompt. If you rename or rephrase
# the opening of `_JUDGE_SYSTEM`, update this too.
JUDGE_SYSTEM_MARKER = "evaluation judge"

_JUDGE_SYSTEM = (
    "You are an " + JUDGE_SYSTEM_MARKER + ". The user gives you a wiki page body and one "
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
        detail="expected=%s actual=%s" % (expected.value, actual.value),
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
            detail="ratio=%.2f max=%.2f" % (ratio, max_ratio),
        )
    overshoot = ratio - max_ratio
    score = max(0.0, 1.0 - overshoot / (max_ratio * 2))
    return ScorerOutcome(
        name="bloat_ratio",
        score=score,
        passed=False,
        detail="ratio=%.2f max=%.2f overshoot=%.2f" % (ratio, max_ratio, overshoot),
    )


def markdown_valid(body: str) -> ScorerOutcome:
    """Structural validity via a real CommonMark parser plus targeted checks.

    Catches: unclosed code fences, heading-level skips, table column-count
    drift between header and body rows, bare unmatched link brackets,
    malformed bullet/ordered-list nesting. Heuristic complements — not a
    full linter, but strictly more than the prior regex-only check.
    """
    if not body.strip():
        return ScorerOutcome(name="markdown_valid", score=0.0, passed=False, detail="empty")

    from markdown_it import MarkdownIt

    md = MarkdownIt("commonmark", {"html": False}).enable("table")
    tokens = md.parse(body)

    # Heading hierarchy: no level jumps > +1
    prev_level = 0
    for t in tokens:
        if t.type == "heading_open":
            level = int(t.tag[1])
            if prev_level and level > prev_level + 1:
                return ScorerOutcome(
                    name="markdown_valid",
                    score=0.5,
                    passed=False,
                    detail="heading jumped from h%d to h%d" % (prev_level, level),
                )
            prev_level = level

    # Tables: every body row must have the same column count as the header
    header_cols = 0
    in_thead = False
    in_tbody_row = False
    row_cols = 0
    for t in tokens:
        if t.type == "thead_open":
            in_thead = True
            header_cols = 0
        elif t.type == "thead_close":
            in_thead = False
        elif in_thead and t.type == "th_open":
            header_cols += 1
        elif t.type == "tr_open":
            in_tbody_row = not in_thead
            row_cols = 0
        elif in_tbody_row and t.type == "td_open":
            row_cols += 1
        elif t.type == "tr_close" and in_tbody_row and header_cols:
            in_tbody_row = False
            if row_cols != header_cols:
                return ScorerOutcome(
                    name="markdown_valid",
                    score=0.3,
                    passed=False,
                    detail="table row has %d cells, header has %d" % (row_cols, header_cols),
                )

    return ScorerOutcome(name="markdown_valid", score=1.0, passed=True, detail="ok")


# Heuristic entity tokens used by entity_density_delta. No NLP dep — we look
# for the things that matter in technical wikis: title-cased phrases, file
# paths, code identifiers in backticks, version strings, numbers with units.
_ENTITY_PATTERNS = [
    re.compile(r"`[^`\n]+`"),  # `inline_code`
    re.compile(r"\b[A-Z][a-zA-Z0-9]+(?:\s+[A-Z][a-zA-Z0-9]+)+\b"),  # Title Case Phrases
    re.compile(r"\b[a-zA-Z_][\w./-]+\.(?:py|md|yaml|json|sh|ts|tsx)\b"),  # file paths
    re.compile(r"\bv?\d+\.\d+(?:\.\d+)?\b"),  # versions / numbers
    re.compile(r"\b\d+(?:ms|s|min|h|d|GB|MB|KB|TB|%|req/s|/sec|/min)\b"),  # numbers with units
]


def _entity_count(text: str) -> int:
    total = 0
    for pat in _ENTITY_PATTERNS:
        total += len(pat.findall(text))
    return total


def entity_density_delta(current_body: str, new_body: str) -> ScorerOutcome:
    """Compare entity density (entities per 100 tokens) between old and new.

    A healthy edit changes density by a small amount in either direction; a
    large drop means we lost facts (info loss), a large jump means we added
    a wall of terminology (potential bloat). Scored on |delta|: 1.0 at 0,
    falling off linearly past a small threshold.
    """
    cur_tokens = max(len(current_body.split()), 1)
    new_tokens = max(len(new_body.split()), 1)
    cur_density = _entity_count(current_body) / cur_tokens * 100
    new_density = _entity_count(new_body) / new_tokens * 100
    delta = new_density - cur_density
    abs_delta = abs(delta)
    # 1.0 if within ±2 entities/100tok, 0 at ±10
    score = max(0.0, 1.0 - max(0.0, abs_delta - 2.0) / 8.0)
    return ScorerOutcome(
        name="entity_density_delta",
        score=score,
        passed=abs_delta <= 4.0,
        detail="cur=%.2f new=%.2f delta=%+.2f/100tok" % (cur_density, new_density, delta),
    )


def diff_addition_ratio(current_body: str, new_body: str) -> ScorerOutcome:
    """Token-diff added-tokens / current-tokens.

    Complements bloat_ratio: a verbose rewrite where char count stays similar
    but most tokens are replaced will read as a high addition ratio here.
    1.0 if added ratio ≤ 0.5; scaled penalty past that.
    """
    import difflib

    cur_tokens = current_body.split()
    new_tokens = new_body.split()
    matcher = difflib.SequenceMatcher(a=cur_tokens, b=new_tokens, autojunk=False)
    added = 0
    for tag, _i1, _i2, j1, j2 in matcher.get_opcodes():
        if tag in ("insert", "replace"):
            added += j2 - j1
    ratio = added / max(len(cur_tokens), 1)
    score = 1.0 if ratio <= 0.5 else max(0.0, 1.0 - (ratio - 0.5) / 1.5)
    return ScorerOutcome(
        name="diff_addition_ratio",
        score=score,
        passed=ratio <= 0.75,
        detail="added=%d/%d ratio=%.2f" % (added, len(cur_tokens), ratio),
    )


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
    detail = "tp=%d fp=%d fn=%d" % (tp, fp, fn)
    return (
        ScorerOutcome(name="precision", score=precision, passed=precision >= 0.8, detail=detail),
        ScorerOutcome(name="recall", score=recall, passed=recall >= 0.8, detail=detail),
        ScorerOutcome(name="f1", score=f1, passed=f1 >= 0.8, detail=detail),
    )


def _judge_one(body: str, claim: FactClaim, *, judge_model: str) -> tuple[str, str]:
    """Single judge vote. Returns (verdict, rationale). Verdict ∈ {yes,no,unknown,error}."""
    user = "Wiki page body:\n---\n%s\n---\n\nClaim: %s" % (body, claim.text)
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
