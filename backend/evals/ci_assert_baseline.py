"""CI guard: read JSONL run files in a directory, fail if structurally broken.

Run by ``evals-smoke.yml`` after each dry-run runner. Catches scorer or
dataset regressions before they hide in a green PR. Intentionally strict:

* Every run file must be readable JSONL.
* Every row must validate against ``CaseResult``.
* Every run must report at least one scorer per case.
* Trigger-class match must be 1.0 in dry-run mode — the stub is an oracle.

Anything stricter (e.g. regression thresholds for facts_present) belongs
in the nightly live job, not the smoke job.
"""

from __future__ import annotations

import sys
from pathlib import Path

from evals.schema import CaseResult


def check_run_file(path: Path) -> list[str]:
    errs: list[str] = []
    if not path.exists() or path.stat().st_size == 0:
        return ["%s missing or empty" % path]
    rows: list[CaseResult] = []
    with path.open() as fh:
        for line_num, line in enumerate(fh, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(CaseResult.model_validate_json(line))
            except Exception as exc:
                errs.append("%s:%d invalid CaseResult: %s" % (path, line_num, exc))
    if not rows:
        errs.append("%s has zero rows" % path)
        return errs
    for r in rows:
        if not r.scorers:
            errs.append("%s case %s missing scorers" % (path, r.case_id))
        trigger = next((s for s in r.scorers if s.name == "trigger_class_match"), None)
        if trigger is not None and trigger.score < 1.0:
            errs.append(
                "%s case %s trigger_class_match=%.2f (expected 1.0 in dry-run)"
                % (path, r.case_id, trigger.score)
            )
    return errs


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: python -m evals.ci_assert_baseline <runs-dir>", file=sys.stderr)
        return 2
    runs_dir = Path(argv[1])
    if not runs_dir.is_dir():
        print("not a directory: %s" % runs_dir, file=sys.stderr)
        return 2
    files = sorted(runs_dir.glob("*.jsonl"))
    if not files:
        print("no JSONL run files in %s" % runs_dir, file=sys.stderr)
        return 2
    all_errs: list[str] = []
    for f in files:
        all_errs.extend(check_run_file(f))
    if all_errs:
        for e in all_errs:
            print("FAIL: %s" % e, file=sys.stderr)
        return 1
    print("ok: %d run file(s) validated" % len(files))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
