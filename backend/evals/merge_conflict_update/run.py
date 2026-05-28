"""CLI: run the merge_conflict_update eval across a model matrix.

    cd backend
    uv run python -m evals.merge_conflict_update.run \\
        --cases evals/datasets/merge_conflict_update/cases \\
        --models claude-sonnet-4-6,gpt-5

Scores three axes per case:

* **Information preservation** — facts the Current edit and the Draft
  edit each contributed must survive into the merged body
  (``facts_from_current_present`` + ``facts_from_draft_present``).
* **No fabrication** — facts not present in any of base/current/draft
  must not appear in the output (``facts_no_hallucination``).
* **Conflict annotation** — when a case marks a direct conflict, the
  merged body must carry an inline source marker
  (``conflict_annotation_present``).

Also reuses ``markdown_valid`` from the shared scorer suite.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import time
from pathlib import Path

from app.utils.logging import setup_logging

from evals import _cli, reporting, scorers
from evals.merge_conflict_update._stub import stub_merge_conflict
from evals.merge_conflict_update.harness import load_cases, run_case
from evals.schema import MergeConflictCase, ScorerOutcome, Surface

log = logging.getLogger(__name__)


# Production agent emits the conflict annotation as a parenthesised inline
# value: e.g. "10k (12k from: <commit message>)" when a commit message is
# available, "10k (12k from another update)" as the fallback. The stub
# emits an HTML comment for tests. The regex matches both production
# forms + the stub form without tripping on bare prose ("migrated from:",
# "inherited from BaseClass") that has no enclosing paren or HTML comment.
_ANNOTATION_RE = re.compile(
    r"""
    \([^)]{1,200}\bfrom\b[^)]{0,200}\)  # paren-wrapped "(... from ...)"
    | <!--[^>]{0,200}\bfrom\b[^>]{0,200}-->  # HTML comment fallback (stub)
    """,
    re.IGNORECASE | re.VERBOSE,
)


def _has_annotation(body: str) -> bool:
    return bool(_ANNOTATION_RE.search(body))


def _score_case(
    case: MergeConflictCase,
    merged: str,
    *,
    judge_models: tuple[str, ...] | None = None,
) -> list[ScorerOutcome]:
    rows: list[ScorerOutcome] = []
    if case.facts_from_current_present:
        fc = scorers.facts_present(
            merged, case.facts_from_current_present, judge_models=judge_models
        )
        rows.append(
            ScorerOutcome(
                name="facts_from_current_present",
                score=fc.score,
                passed=fc.passed,
                detail=fc.detail,
            )
        )
    if case.facts_from_draft_present:
        fd = scorers.facts_present(merged, case.facts_from_draft_present, judge_models=judge_models)
        rows.append(
            ScorerOutcome(
                name="facts_from_draft_present",
                score=fd.score,
                passed=fd.passed,
                detail=fd.detail,
            )
        )
    if case.facts_must_not_appear:
        # Same judge — YES means the unwanted fact IS in the body, flip.
        fh = scorers.facts_present(merged, case.facts_must_not_appear, judge_models=judge_models)
        flipped = 1.0 - fh.score
        rows.append(
            ScorerOutcome(
                name="facts_no_hallucination",
                score=flipped,
                passed=flipped >= 0.9,
                detail=fh.detail,
            )
        )
    if case.expects_conflict_annotation:
        present = _has_annotation(merged)
        rows.append(
            ScorerOutcome(
                name="conflict_annotation_present",
                score=1.0 if present else 0.0,
                passed=present,
                detail="pattern=%s" % _ANNOTATION_RE.pattern,
            )
        )
    rows.append(scorers.markdown_valid(merged))
    return rows


def _make_run_one(
    judge_models: tuple[str, ...] | None,
) -> _cli.RunOne[MergeConflictCase]:
    def _run_one(
        case: MergeConflictCase, provider: str, model: str, run_index: int
    ) -> tuple[Surface, str, str, str, list[ScorerOutcome]]:
        del provider, run_index
        merged = run_case(case)
        rows = _score_case(case, merged, judge_models=judge_models)
        expected = "merged"
        actual = "merged" if merged else "<empty>"
        raw = json.dumps({"merged_body": merged})
        return ("merge_conflict_update", expected, actual, raw, rows)

    return _run_one


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Run the merge_conflict_update eval.")
    p.add_argument(
        "--cases",
        type=Path,
        default=Path("evals/datasets/merge_conflict_update/cases"),
        help="Directory of merge-conflict case YAML files",
    )
    p.add_argument(
        "--judge-models",
        default=",".join(scorers.DEFAULT_JUDGE_PANEL),
        help="Comma-separated judge model panel for fact scoring.",
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
        "running %d merge_conflict cases × %d models × %d runs (concurrency=%d)",
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
        dry_run_ctx=stub_merge_conflict(cases) if args.dry_run else None,
    )

    out_path = args.out or Path("runs") / ("merge_conflict_%d.jsonl" % int(time.time()))
    reporting.write_jsonl(out_path, results)
    summary = reporting.summarize(results, surface="merge_conflict_update")
    reporting.print_summary(summary)
    bt_url = ""
    if args.braintrust:
        bt_url = reporting.push_to_braintrust(args.braintrust, results, dataset=args.dataset)
    reporting.write_github_summary(summary, braintrust_url=bt_url)
    print(json.dumps({"out": str(out_path), "skipped_models": skipped, "braintrust_url": bt_url}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
