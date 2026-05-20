"""Result IO and pretty-printing for eval runs.

A run produces one JSONL file with one ``CaseResult`` per line. A summary
table is printed to stdout in every run. The optional Braintrust push
uploads the same case results as an experiment so the model matrix is
visible alongside the live traces that come out of ``app.tracing``.
"""

from __future__ import annotations

import logging
import os
import statistics
import sys
from pathlib import Path
from typing import IO, Iterable

from app.tracing import braintrust as bt
from evals.schema import CaseResult, RunSummary, Surface


log = logging.getLogger(__name__)


def write_jsonl(path: Path, results: Iterable[CaseResult]) -> int:
    """Write results to ``path`` (one JSON per line). Returns count written."""
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w") as fh:
        for r in results:
            fh.write(r.model_dump_json())
            fh.write("\n")
            count += 1
    log.info("wrote %d results to %s", count, path)
    return count


def summarize(results: list[CaseResult], surface: Surface) -> RunSummary:
    """Reduce per-case results to per-model averages per scorer."""
    models = sorted({r.model for r in results})
    per_model: dict[str, dict[str, float]] = {}
    for m in models:
        rows = [r for r in results if r.model == m]
        scorer_names = sorted({s.name for r in rows for s in r.scorers})
        per_model[m] = {}
        for sn in scorer_names:
            scores = [s.score for r in rows for s in r.scorers if s.name == sn]
            per_model[m][sn] = statistics.fmean(scores) if scores else 0.0
        per_model[m]["error_rate"] = sum(1 for r in rows if r.error) / max(len(rows), 1)
    return RunSummary(
        surface=surface,
        models=models,
        case_count=len({r.case_id for r in results}),
        per_model=per_model,
    )


def print_summary(summary: RunSummary, *, stream: IO[str] = sys.stdout) -> None:
    """Print a markdown table to stdout. Stable order = easy diff between runs."""
    scorer_names = sorted({s for m in summary.per_model.values() for s in m.keys()})
    header = "| model | " + " | ".join(scorer_names) + " |"
    divider = "| -- | " + " | ".join("--" for _ in scorer_names) + " |"
    print(f"\nsurface={summary.surface} cases={summary.case_count}\n", file=stream)
    print(header, file=stream)
    print(divider, file=stream)
    for model in summary.models:
        row = summary.per_model[model]
        cells = [f"{row.get(sn, 0):.2f}" for sn in scorer_names]
        print(f"| {model} | " + " | ".join(cells) + " |", file=stream)
    print("", file=stream)


def push_to_braintrust(
    experiment: str,
    results: list[CaseResult],
    *,
    project: str | None = None,
) -> None:
    """Upload results as a Braintrust experiment.

    Routes through ``app.tracing.braintrust.push_experiment`` so the
    ``braintrust`` SDK is only imported behind the single allowed seam.
    Keys come from ``BRAINTRUST_API_KEY`` and ``BRAINTRUST_PROJECT`` env
    vars by default. Missing config is a no-op with a warning.
    """
    api_key = os.environ.get("BRAINTRUST_API_KEY", "")
    project_name = project or os.environ.get("BRAINTRUST_PROJECT", "")
    if not api_key or not project_name:
        log.warning("braintrust: skip push — no BRAINTRUST_API_KEY or BRAINTRUST_PROJECT")
        return
    rows = [
        bt.ExperimentRow(
            input={"case_id": r.case_id, "surface": r.surface},
            output={"actual_class": r.actual_class, "raw_output": r.raw_output},
            expected={"expected_class": r.expected_class},
            scores={s.name: s.score for s in r.scorers},
            metadata={
                "provider": r.provider,
                "model": r.model,
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
    log.info(
        "braintrust: pushed %d/%d results to project=%s experiment=%s",
        pushed,
        len(results),
        project_name,
        experiment,
    )
