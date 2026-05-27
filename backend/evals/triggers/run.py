"""CLI: run the trigger-firing eval across a model matrix.

    cd backend
    uv run python -m evals.triggers.run \\
        --cases evals/datasets/triggers/cases \\
        --models claude-sonnet-4-6,gpt-5

The trigger eval measures two axes per case:

* **WHEN** — phase-1 firing-condition decision (``trigger_match_decision``,
  ``no_false_fire_compliance`` aggregate). False positives are louder than
  false negatives in production, so a separate scorer tracks how often we
  fire on cases the ground truth says should stay silent.
* **HOW** — phase-2 rendered message quality on matched cases
  (``message_facts_present``, ``message_facts_excluded``, ``message_bloat_ratio``,
  ``reason_facts_present``). Reuses the shared judge panel.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

from app.utils.logging import setup_logging

from evals import _cli, reporting, scorers
from evals.schema import ScorerOutcome, Surface, TriggerCase
from evals.triggers._stub import stub_triggers
from evals.triggers.harness import TriggerRunResult, load_cases, run_case


log = logging.getLogger(__name__)


def _score_case(
    case: TriggerCase,
    out: TriggerRunResult,
    *,
    judge_models: tuple[str, ...] | None = None,
) -> list[ScorerOutcome]:
    rows: list[ScorerOutcome] = []

    match_ok = out.matched == case.expected_matched
    rows.append(
        ScorerOutcome(
            name="trigger_match_decision",
            score=1.0 if match_ok else 0.0,
            passed=match_ok,
            detail="expected=%s actual=%s" % (case.expected_matched, out.matched),
        )
    )

    # no_false_fire: penalize the dangerous direction only (false positives).
    # On cases the ground truth says should NOT fire, did we fire anyway?
    if not case.expected_matched:
        no_false = not out.matched
        rows.append(
            ScorerOutcome(
                name="no_false_fire_compliance",
                score=1.0 if no_false else 0.0,
                passed=no_false,
                detail="should_not_fire actual_matched=%s" % out.matched,
            )
        )
    else:
        rows.append(
            ScorerOutcome(
                name="no_false_fire_compliance",
                score=1.0,
                passed=True,
                detail="n/a (case expects fire)",
            )
        )

    # Reason quality: only meaningful when phase 1 fired and the case
    # has labeled facts the reason should cite (delta/schedule paths).
    if out.matched and case.expected_reason_facts and out.reason:
        fp = scorers.facts_present(
            out.reason, case.expected_reason_facts, judge_models=judge_models
        )
        rows.append(
            ScorerOutcome(
                name="reason_facts_present",
                score=fp.score,
                passed=fp.passed,
                detail=fp.detail,
            )
        )
    else:
        rows.append(
            ScorerOutcome(
                name="reason_facts_present",
                score=1.0,
                passed=True,
                detail="n/a",
            )
        )

    # Message quality scorers run only when phase 1 fired AND we have a
    # rendered message (some cases test only the WHEN axis).
    if out.matched and out.message:
        if case.expected_message_facts_present:
            mp = scorers.facts_present(
                out.message,
                case.expected_message_facts_present,
                judge_models=judge_models,
            )
            rows.append(
                ScorerOutcome(
                    name="message_facts_present",
                    score=mp.score,
                    passed=mp.passed,
                    detail=mp.detail,
                )
            )
        if case.expected_message_facts_excluded:
            # facts_excluded uses the SAME judge — verdict YES (claim
            # supported in the body) means we leaked something we shouldn't
            # have, so we flip the score.
            mx = scorers.facts_present(
                out.message,
                case.expected_message_facts_excluded,
                judge_models=judge_models,
            )
            flipped = 1.0 - mx.score
            rows.append(
                ScorerOutcome(
                    name="message_facts_excluded",
                    score=flipped,
                    passed=flipped >= 0.9,
                    detail=mx.detail,
                )
            )
        if case.message_instruction:
            br = scorers.bloat_ratio(
                case.message_instruction,
                out.message,
                max_ratio=case.max_message_bloat_ratio,
            )
            rows.append(
                ScorerOutcome(
                    name="message_bloat_ratio",
                    score=br.score,
                    passed=br.passed,
                    detail=br.detail,
                )
            )
    return rows


def _make_run_one(
    judge_models: tuple[str, ...] | None,
) -> _cli.RunOne[TriggerCase]:
    def _run_one(
        case: TriggerCase, provider: str, model: str, run_index: int
    ) -> tuple[Surface, str, str, str, list[ScorerOutcome]]:
        del provider, run_index
        out = run_case(case)
        rows = _score_case(case, out, judge_models=judge_models)
        expected = "matched=%s" % case.expected_matched
        actual = "matched=%s" % out.matched
        raw = json.dumps(
            {
                "matched": out.matched,
                "reason": out.reason,
                "message": out.message,
                "flavor": case.flavor.value,
            }
        )
        return ("triggers", expected, actual, raw, rows)

    return _run_one


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Run the trigger-firing eval.")
    p.add_argument(
        "--cases",
        type=Path,
        default=Path("evals/datasets/triggers/cases"),
        help="Directory of trigger case YAML files",
    )
    p.add_argument(
        "--judge-models",
        default=",".join(scorers.DEFAULT_JUDGE_PANEL),
        help="Comma-separated judge model panel for fact/reason scoring.",
    )
    _cli.add_common_args(p, default_models="claude-sonnet-4-6")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv or sys.argv[1:])
    setup_logging(args.log_level)
    cases = load_cases(args.cases)
    if args.case_id:
        cases = [c for c in cases if c.id == args.case_id]
        if not cases:
            log.error("no case with id %s", args.case_id)
            return 2
    if args.limit is not None:
        cases = cases[: args.limit]

    runnable, skipped = _cli.resolve_runnable(args.models, dry_run=args.dry_run)
    if not runnable:
        log.error("no runnable models — set EVAL_*_API_KEY or pass --dry-run")
        return 2
    metadata = _cli.build_metadata(Path(__file__), args.cases)
    judge_panel = tuple(j.strip() for j in args.judge_models.split(",") if j.strip())

    log.info(
        "running %d trigger cases × %d models × %d runs (concurrency=%d)",
        len(cases),
        len(runnable),
        args.runs,
        args.concurrency,
    )
    results = _cli.run_concurrent(
        cases,
        runnable=runnable,
        runs=args.runs,
        run_one=_make_run_one(judge_panel),
        case_id=lambda c: c.id,
        metadata=metadata,
        judge_models=list(judge_panel),
        concurrency=args.concurrency,
        dry_run_ctx=stub_triggers(cases) if args.dry_run else None,
    )

    out_path = args.out or Path("runs") / ("triggers_%d.jsonl" % int(time.time()))
    reporting.write_jsonl(out_path, results)
    summary = reporting.summarize(results, surface="triggers")
    reporting.print_summary(summary)
    bt_url = ""
    if args.braintrust:
        bt_url = reporting.push_to_braintrust(args.braintrust, results, dataset=args.dataset)
    reporting.write_github_summary(summary, braintrust_url=bt_url)
    print(json.dumps({"out": str(out_path), "skipped_models": skipped, "braintrust_url": bt_url}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
