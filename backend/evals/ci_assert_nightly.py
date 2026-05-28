"""CI guard: enforce per-surface scorer thresholds on a nightly run.

Run by ``evals-nightly.yml`` after the live model matrix finishes. Catches
silent quality regressions from prompt churn — without this, a prompt PR
can land that drops ``facts_preserved`` 15 points and only show up when
someone notices the BT chart trending down.

Thresholds are deliberate floor values, not aspirations. They sit one
broad bucket below the current baseline so they catch real regressions
without flapping on per-run noise. Update them upward when a sustained
improvement lands; keep the comment trail (`baseline @ <date>`) so the
reasoning isn't lost.

Run-file → surface inference is filename-based to match the writer
convention in `evals-nightly.yml`:

    runs/nightly_wiki_updater.jsonl   → wiki_updater (both sub-surfaces)
    runs/nightly_ingest_selector.jsonl → ingest_selector
    runs/nightly_external_agent.jsonl  → external_agent
    runs/nightly_triggers.jsonl        → triggers

Per-model aggregation: arithmetic mean of per-case means. Skipped if the
file is empty or the surface has no threshold entry.
"""

from __future__ import annotations

import logging
import statistics
import sys
from collections import defaultdict
from pathlib import Path

from app.utils.logging import setup_logging

from evals.schema import CaseResult

log = logging.getLogger(__name__)


# floor scores per surface per scorer. fail the run if mean dips below.
# Anchored to the 2026-05-28 nightly baseline minus one bucket of slack.
# When you improve a score sustainedly, raise the floor in the same PR.
SURFACE_THRESHOLDS: dict[str, dict[str, float]] = {
    "external_agent": {
        # baseline @ 2026-05-28: claude=0.889, gpt-5=0.859
        "facts_preserved_avg": 0.80,
        # baseline @ 2026-05-28: claude=0.913, gpt-5=0.904
        "facts_present_avg": 0.85,
        # baseline @ 2026-05-28: ≥ 0.98 both models
        "update_f1": 0.95,
        # baseline @ 2026-05-28: 0.99 both models
        "no_touch_compliance": 0.90,
    },
    "process_instruction": {
        # baseline @ 2026-05-28: claude=1.00, gpt-5=0.882
        "trigger_class_match": 0.80,
        # baseline @ 2026-05-28: claude=0.939, gpt-5=0.923
        "facts_present": 0.85,
        # baseline @ 2026-05-28: claude=0.970, gpt-5=0.949
        "facts_preserved": 0.85,
    },
    "reconcile_document": {
        # baseline @ 2026-05-28: claude=0.527, gpt-5=0.580 — known broken,
        # tracked in eval-findings doc. Floor sits below both so further
        # collapse is still caught.
        "trigger_class_match": 0.45,
        # baseline @ 2026-05-28: claude=0.917, gpt-5=0.946
        "facts_present": 0.80,
        # baseline @ 2026-05-28: claude=0.993, gpt-5=0.974
        "facts_preserved": 0.85,
    },
    "ingest_selector": {
        # baseline @ 2026-05-28: haiku=0.534, gpt-5-mini=0.867 — wide
        # spread. Floor sits below haiku so the matrix passes; the haiku
        # gap is tracked separately (move to gpt-5-mini, or tune the
        # selector prompt for haiku).
        "f1": 0.45,
        "recall": 0.80,
    },
    "triggers": {
        # baseline @ 2026-05-28: 0.97–1.00 across all scorers, both models
        "trigger_match_decision": 0.85,
        "no_false_fire_compliance": 0.90,
    },
}


_FILENAME_TO_SURFACES: dict[str, tuple[str, ...]] = {
    "nightly_wiki_updater.jsonl": ("process_instruction", "reconcile_document"),
    "nightly_ingest_selector.jsonl": ("ingest_selector",),
    "nightly_external_agent.jsonl": ("external_agent",),
    "nightly_triggers.jsonl": ("triggers",),
}


def _load_rows(path: Path) -> list[CaseResult]:
    rows: list[CaseResult] = []
    with path.open() as fh:
        for line_num, line in enumerate(fh, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(CaseResult.model_validate_json(line))
            except Exception as exc:
                raise ValueError("%s:%d invalid CaseResult: %s" % (path, line_num, exc)) from exc
    return rows


def _means_per_model(rows: list[CaseResult], scorer_name: str, surface: str) -> dict[str, float]:
    """For one scorer, return {model: case-level mean score} across all cases.

    Case-level (not run-level) so a high-variance case doesn't outweigh
    a stable one. Mirrors the aggregation in ``reporting.summarize``.
    Only rows whose ``surface`` matches are counted, so wiki_updater's
    two sub-surfaces stay distinct.
    """
    per_case: dict[tuple[str, str], list[float]] = defaultdict(list)
    for r in rows:
        if r.surface != surface:
            continue
        for s in r.scorers:
            if s.name == scorer_name:
                per_case[(r.model, r.case_id)].append(s.score)
    by_model: dict[str, list[float]] = defaultdict(list)
    for (model, _), vs in per_case.items():
        if vs:
            by_model[model].append(statistics.fmean(vs))
    return {m: statistics.fmean(vs) for m, vs in by_model.items() if vs}


def check_run_file(path: Path) -> list[str]:
    """Return human-readable failure strings; empty list = clean."""
    errs: list[str] = []
    if not path.exists() or path.stat().st_size == 0:
        return ["%s missing or empty" % path]
    surfaces = _FILENAME_TO_SURFACES.get(path.name)
    if surfaces is None:
        log.info("skip %s — no threshold mapping", path.name)
        return []
    try:
        rows = _load_rows(path)
    except ValueError as exc:
        return [str(exc)]
    if not rows:
        return ["%s has zero rows" % path]
    for surface in surfaces:
        thresholds = SURFACE_THRESHOLDS.get(surface)
        if not thresholds:
            continue
        for scorer, floor in thresholds.items():
            means = _means_per_model(rows, scorer, surface)
            if not means:
                continue
            for model, mean in sorted(means.items()):
                if mean < floor:
                    errs.append(
                        "%s surface=%s model=%s %s=%.3f below floor %.3f"
                        % (path.name, surface, model, scorer, mean, floor)
                    )
    return errs


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: python -m evals.ci_assert_nightly <runs-dir>", file=sys.stderr)
        return 2
    runs_dir = Path(argv[1])
    if not runs_dir.is_dir():
        print("not a directory: %s" % runs_dir, file=sys.stderr)
        return 2
    setup_logging("INFO")
    files = sorted(runs_dir.glob("nightly_*.jsonl"))
    if not files:
        print("no nightly_*.jsonl run files in %s" % runs_dir, file=sys.stderr)
        return 2
    all_errs: list[str] = []
    for f in files:
        all_errs.extend(check_run_file(f))
    if all_errs:
        for e in all_errs:
            print("FAIL: %s" % e, file=sys.stderr)
        return 1
    print("ok: %d nightly run file(s) above all thresholds" % len(files))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
