"""Result IO, bootstrap confidence intervals, and pretty-printing for runs."""

from __future__ import annotations

import logging
import os
import random
import statistics
import sys
from collections import defaultdict
from pathlib import Path
from typing import IO, Iterable

from app.tracing import braintrust as bt
from evals.schema import CaseResult, RunSummary, ScorerSummary, Surface


log = logging.getLogger(__name__)


def write_jsonl(path: Path, results: Iterable[CaseResult]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w") as fh:
        for r in results:
            fh.write(r.model_dump_json())
            fh.write("\n")
            count += 1
    log.info("wrote %d results to %s", count, path)
    return count


def _paired_bootstrap_ci(
    case_scores: list[float], *, iterations: int = 1000, alpha: float = 0.05, seed: int = 42
) -> tuple[float, float]:
    """Resample case-level scores with replacement; return (lo, hi) CI on the mean.

    Case-level (not run-level) resampling so the CI reflects between-case
    variance — the dominant source of uncertainty at our sample sizes.
    """
    if not case_scores:
        return (0.0, 0.0)
    rng = random.Random(seed)
    n = len(case_scores)
    means: list[float] = []
    for _ in range(iterations):
        sample = [case_scores[rng.randrange(n)] for _ in range(n)]
        means.append(sum(sample) / n)
    means.sort()
    lo_idx = max(0, int(iterations * (alpha / 2)))
    hi_idx = min(iterations - 1, int(iterations * (1 - alpha / 2)))
    return (means[lo_idx], means[hi_idx])


def _case_level_scores(rows: list[CaseResult], scorer_name: str) -> list[float]:
    """Mean each case's score across its k runs, then return the per-case list."""
    by_case: dict[str, list[float]] = defaultdict(list)
    for r in rows:
        for s in r.scorers:
            if s.name == scorer_name:
                by_case[r.case_id].append(s.score)
    return [statistics.fmean(v) for v in by_case.values() if v]


def _trigger_class_metrics(rows: list[CaseResult]) -> list[ScorerSummary]:
    """Per-class precision/recall + macro-F1 for the trigger trinary.

    Computed across all (case, run) rows for one model. Treats each
    distinct value of `expected_class` as a class. The per-class
    precision/recall surfaces the cost asymmetry that a single accuracy
    number hides — e.g. flipping IRRELEVANT → CHANGE (false action) vs
    flipping CHANGE → NO_CHANGE (missed update) score the same on
    accuracy but mean very different things in production.
    """
    classes = sorted({r.expected_class for r in rows} | {r.actual_class for r in rows})
    summaries: list[ScorerSummary] = []
    f1_per_class: list[float] = []
    for cls in classes:
        tp = sum(1 for r in rows if r.expected_class == cls and r.actual_class == cls)
        fp = sum(1 for r in rows if r.expected_class != cls and r.actual_class == cls)
        fn = sum(1 for r in rows if r.expected_class == cls and r.actual_class != cls)
        precision = tp / (tp + fp) if (tp + fp) else 1.0
        recall = tp / (tp + fn) if (tp + fn) else 1.0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
        f1_per_class.append(f1)
        for name, value in (
            (f"trigger.precision[{cls}]", precision),
            (f"trigger.recall[{cls}]", recall),
            (f"trigger.f1[{cls}]", f1),
        ):
            summaries.append(
                ScorerSummary(
                    name=name,
                    mean=value,
                    ci_low=value,
                    ci_high=value,
                    n_cases=len({r.case_id for r in rows}),
                    n_runs_per_case=0,
                )
            )
    if f1_per_class:
        macro = statistics.fmean(f1_per_class)
        summaries.append(
            ScorerSummary(
                name="trigger.macro_f1",
                mean=macro,
                ci_low=macro,
                ci_high=macro,
                n_cases=len({r.case_id for r in rows}),
                n_runs_per_case=0,
            )
        )
    return summaries


def summarize(results: list[CaseResult], surface: Surface) -> RunSummary:
    models = sorted({r.model for r in results})
    runs_per_case = 0
    if results:
        by_case_model: dict[tuple[str, str], int] = defaultdict(int)
        for r in results:
            by_case_model[(r.case_id, r.model)] += 1
        runs_per_case = max(by_case_model.values())

    per_model: dict[str, list[ScorerSummary]] = {}
    for m in models:
        rows = [r for r in results if r.model == m]
        scorer_names = sorted({s.name for r in rows for s in r.scorers})
        summaries: list[ScorerSummary] = []
        for sn in scorer_names:
            case_scores = _case_level_scores(rows, sn)
            mean = statistics.fmean(case_scores) if case_scores else 0.0
            lo, hi = _paired_bootstrap_ci(case_scores)
            summaries.append(
                ScorerSummary(
                    name=sn,
                    mean=mean,
                    ci_low=lo,
                    ci_high=hi,
                    n_cases=len(case_scores),
                    n_runs_per_case=runs_per_case,
                )
            )
        # Per-class trigger metrics — only meaningful when the surface emits
        # trigger_class_match. Skip for surfaces that don't (e.g. ingest_selector).
        if any(s.name == "trigger_class_match" for r in rows for s in r.scorers):
            summaries.extend(_trigger_class_metrics(rows))
        # error_rate across all rows (not per-case averaged).
        err_cases = {r.case_id for r in rows if r.error}
        total_cases = {r.case_id for r in rows}
        err_rate = len(err_cases) / max(len(total_cases), 1)
        summaries.append(
            ScorerSummary(
                name="error_rate",
                mean=err_rate,
                ci_low=err_rate,
                ci_high=err_rate,
                n_cases=len(total_cases),
                n_runs_per_case=runs_per_case,
            )
        )
        per_model[m] = summaries

    return RunSummary(
        surface=surface,
        models=models,
        case_count=len({r.case_id for r in results}),
        runs_per_case=runs_per_case,
        per_model=per_model,
    )


def _render_markdown_table(summary: RunSummary) -> list[str]:
    """Render a per-model scorer table as a list of markdown lines.

    Shared between stdout printing and the GitHub-step-summary writer so
    one truth shapes the row formatting (mean + bootstrap CI).
    """
    scorer_names = sorted({s.name for ss in summary.per_model.values() for s in ss})
    lines: list[str] = []
    lines.append("| model | " + " | ".join(scorer_names) + " |")
    lines.append("| -- | " + " | ".join("--" for _ in scorer_names) + " |")
    for model in summary.models:
        by_name = {s.name: s for s in summary.per_model[model]}
        cells: list[str] = []
        for sn in scorer_names:
            s = by_name.get(sn)
            if s is None:
                cells.append("-")
            else:
                cells.append("%.2f [%.2f, %.2f]" % (s.mean, s.ci_low, s.ci_high))
        lines.append("| %s | %s |" % (model, " | ".join(cells)))
    return lines


def print_summary(summary: RunSummary, *, stream: IO[str] = sys.stdout) -> None:
    print(
        "\nsurface=%s cases=%d runs/case=%d\n"
        % (summary.surface, summary.case_count, summary.runs_per_case),
        file=stream,
    )
    for line in _render_markdown_table(summary):
        print(line, file=stream)
    print("", file=stream)


def push_to_braintrust(
    experiment: str,
    results: list[CaseResult],
    *,
    project: str | None = None,
) -> str:
    """Push results, return the experiment URL (or "" if push was skipped)."""
    api_key = os.environ.get("BRAINTRUST_API_KEY", "")
    project_name = project or os.environ.get("BRAINTRUST_PROJECT", "")
    if not api_key or not project_name:
        log.warning("braintrust: skip push — no BRAINTRUST_API_KEY or BRAINTRUST_PROJECT")
        return ""
    rows = [
        bt.ExperimentRow(
            input={"case_id": r.case_id, "surface": r.surface, "run_index": r.run_index},
            output={"actual_class": r.actual_class, "raw_output": r.raw_output},
            expected={"expected_class": r.expected_class},
            scores={s.name: s.score for s in r.scorers},
            metadata={
                "provider": r.provider,
                "model": r.model,
                "run_index": r.run_index,
                "error": r.error,
                "latency_ms": r.latency_ms,
                "input_tokens": r.input_tokens,
                "output_tokens": r.output_tokens,
            },
        )
        for r in results
    ]
    pushed = bt.push_experiment(
        project=project_name,
        experiment=experiment,
        api_key=api_key,
        rows=rows,
    )
    org = os.environ.get("BRAINTRUST_ORG", "")
    if org:
        url = "https://www.braintrust.dev/app/%s/p/%s/experiments/%s" % (
            org.replace(" ", "%20"),
            project_name,
            experiment,
        )
    else:
        url = ""
    log.info(
        "braintrust: pushed %d/%d results to project=%s experiment=%s url=%s",
        pushed,
        len(results),
        project_name,
        experiment,
        url,
    )
    return url


def write_github_summary(
    summary: RunSummary,
    *,
    braintrust_url: str = "",
    title: str | None = None,
) -> None:
    """Append a markdown section to ``$GITHUB_STEP_SUMMARY`` if it's set.

    No-op outside GitHub Actions (env var absent). Renders the same scorer
    table as ``print_summary`` so CI viewers see eval results inline.
    """
    path = os.environ.get("GITHUB_STEP_SUMMARY", "")
    if not path:
        return
    heading = title or "Eval: %s" % summary.surface
    lines: list[str] = ["## %s" % heading, ""]
    lines.append(
        "_cases=%d, runs/case=%d, models=%s_"
        % (summary.case_count, summary.runs_per_case, ", ".join(summary.models))
    )
    if braintrust_url:
        lines.append("")
        lines.append("[Braintrust experiment ↗](%s)" % braintrust_url)
    lines.append("")
    lines.extend(_render_markdown_table(summary))
    lines.append("")
    with open(path, "a", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
