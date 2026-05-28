"""CI guard: enforce per-surface scorer thresholds on a nightly run.

Run by ``evals-nightly.yml`` after the live model matrix finishes. Catches
silent quality regressions from prompt churn — without this, a prompt PR
can land that drops ``facts_preserved`` 15 points and only show up when
someone notices the BT chart trending down.

Thresholds are deliberate floor values, not aspirations. They sit one
broad bucket below the current baseline so they catch real regressions
without flapping on per-run noise.

Run-file → surface inference is filename-based. Every entry in
``_FILENAME_TO_SURFACES`` is required: the asserter fails if an
expected file is missing from the runs directory, which catches the
case where a nightly eval step exits 0 but never writes its output
(wrong ``--out`` path, partial failure, etc.).

Per-model aggregation: arithmetic mean of per-case means.
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
# Each floor sits one bucket below the most recent observed baseline.
# Raise the floor in the PR that lands a sustained improvement.
SURFACE_THRESHOLDS: dict[str, dict[str, float]] = {
    "external_agent": {
        "facts_preserved_avg": 0.80,
        "facts_present_avg": 0.85,
        "update_f1": 0.95,
        "no_touch_compliance": 0.90,
    },
    "process_instruction": {
        "trigger_class_match": 0.80,
        "facts_present": 0.85,
        "facts_preserved": 0.85,
    },
    "reconcile_document": {
        # Known degraded — see the eval-findings doc. Floor sits below
        # the observed range so further collapse still trips.
        "trigger_class_match": 0.45,
        "facts_present": 0.80,
        "facts_preserved": 0.85,
    },
    "ingest_selector": {
        # Wide spread between models — floor below the weaker tail.
        "f1": 0.45,
        "recall": 0.80,
    },
    "triggers": {
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
        # A mapped surface with zero rows in a non-empty file means the
        # run step silently produced no data for it (wrong surface tag, a
        # harness bug mislabelling rows). Fail loudly rather than skip
        # every scorer for the surface — this is the gap that would
        # otherwise let a whole surface's regression pass unnoticed.
        if not any(r.surface == surface for r in rows):
            errs.append("%s expected surface=%s but no rows carry it" % (path.name, surface))
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
    all_errs: list[str] = []
    # Every expected file in the mapping must exist — a missing file means
    # a nightly step silently dropped its output, not "nothing to check".
    for expected in sorted(_FILENAME_TO_SURFACES):
        all_errs.extend(check_run_file(runs_dir / expected))
    # Surface ad-hoc nightly_*.jsonl files (not in the expected set) for
    # diagnostics, but only if mapped — unmapped files are skipped via the
    # ``_FILENAME_TO_SURFACES.get`` check inside ``check_run_file``.
    extra_files = [
        p for p in sorted(runs_dir.glob("nightly_*.jsonl")) if p.name not in _FILENAME_TO_SURFACES
    ]
    for f in extra_files:
        all_errs.extend(check_run_file(f))
    if all_errs:
        for e in all_errs:
            print("FAIL: %s" % e, file=sys.stderr)
        return 1
    print(
        "ok: %d nightly run file(s) above all thresholds"
        % (len(_FILENAME_TO_SURFACES) + len(extra_files))
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
