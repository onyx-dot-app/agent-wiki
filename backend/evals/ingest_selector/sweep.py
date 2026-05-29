"""CLI: sweep the ingest BM25 pre-filter threshold over a labeled set.

    cd backend
    uv run python -m evals.ingest_selector.sweep \\
        --samples evals/datasets/ingest_selector/retrieval_samples.jsonl \\
        --min-retained 0.95

The pre-filter drops a candidate wiki page before it reaches the
reconciler LLM when its BM25 score is below a cutoff (``INGEST_BM25_MIN_SCORE``
in production). Raising the cutoff filters more irrelevant pages — saving
reconciler LLM calls — but risks dropping relevant ones.

This sweep turns that tradeoff into a repeatable eval instead of a
one-off analysis script. For each threshold it reports, over the labeled
set:

* ``irrelevant_filtered`` — fraction of irrelevant pages dropped (higher
  = more LLM cost saved)
* ``relevant_retained`` — fraction of relevant pages kept (higher = fewer
  missed updates; this is the cost of filtering)

It then recommends the highest cutoff that still retains at least
``--min-retained`` of relevant pages — the production operating point.

Input is a JSONL of ``RetrievalSample`` rows. Scores are cached in the
dataset so the sweep is offline + reproducible; refreshing them against
live OpenSearch is a separate step (see ``backend/evals/README.md``).
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from app.utils.logging import setup_logging

from evals.schema import RetrievalSample

log = logging.getLogger(__name__)


def _load_samples(path: Path) -> list[RetrievalSample]:
    rows: list[RetrievalSample] = []
    with path.open() as fh:
        for line_num, line in enumerate(fh, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(RetrievalSample.model_validate_json(line))
            except Exception as exc:
                raise ValueError("invalid sample on line %d: %s" % (line_num, exc)) from exc
    if not rows:
        raise ValueError("no samples loaded from %s" % path)
    return rows


class ThresholdPoint(BaseModel):
    """One point on the sweep curve."""

    model_config = ConfigDict(frozen=True)

    threshold: float
    relevant_retained: float
    irrelevant_filtered: float
    n_relevant: int
    n_irrelevant: int


def sweep(samples: list[RetrievalSample], thresholds: list[float]) -> list[ThresholdPoint]:
    """Compute retained / filtered fractions at each threshold.

    A candidate is KEPT when ``bm25_score >= threshold``. Relevant-retained
    is over relevant samples; irrelevant-filtered is over irrelevant ones.
    Both denominators are fixed (the labeled counts), so the two curves are
    directly comparable across thresholds.
    """
    relevant = [s.bm25_score for s in samples if s.relevant]
    irrelevant = [s.bm25_score for s in samples if not s.relevant]
    n_rel = len(relevant)
    n_irr = len(irrelevant)
    points: list[ThresholdPoint] = []
    for t in sorted(thresholds):
        kept_rel = sum(1 for sc in relevant if sc >= t)
        kept_irr = sum(1 for sc in irrelevant if sc >= t)
        points.append(
            ThresholdPoint(
                threshold=t,
                relevant_retained=(kept_rel / n_rel) if n_rel else 1.0,
                irrelevant_filtered=((n_irr - kept_irr) / n_irr) if n_irr else 0.0,
                n_relevant=n_rel,
                n_irrelevant=n_irr,
            )
        )
    return points


def recommend(points: list[ThresholdPoint], *, min_retained: float) -> ThresholdPoint | None:
    """Highest threshold whose relevant-retained is >= ``min_retained``.

    Higher threshold = more irrelevant filtered, so among all thresholds
    that meet the retention floor we want the largest. Returns None if no
    threshold meets the floor (retention drops below it even at t=0).
    """
    eligible = [p for p in points if p.relevant_retained >= min_retained]
    if not eligible:
        return None
    return max(eligible, key=lambda p: p.threshold)


def _default_thresholds() -> list[float]:
    # 0,2,4,…,60 — covers the production default (20) with headroom either
    # side. Override with --thresholds for a finer or wider grid.
    return [float(t) for t in range(0, 62, 2)]


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Sweep the ingest BM25 pre-filter threshold.")
    p.add_argument(
        "--samples",
        type=Path,
        default=Path("evals/datasets/ingest_selector/retrieval_samples.jsonl"),
        help="JSONL of RetrievalSample rows (cached BM25 score + relevance label)",
    )
    p.add_argument(
        "--thresholds",
        default="",
        help="Comma-separated thresholds to sweep (default: 0..60 step 2)",
    )
    p.add_argument(
        "--min-retained",
        type=float,
        default=0.95,
        help="Minimum relevant-retained fraction for the recommended operating point",
    )
    p.add_argument("--out", type=Path, default=None, help="Optional JSONL sink for the curve")
    p.add_argument("--log-level", default="INFO")
    return p.parse_args(argv)


def _print_curve(points: list[ThresholdPoint], rec: ThresholdPoint | None) -> None:
    print("\ningest BM25 pre-filter sweep")
    print("| threshold | irrelevant_filtered | relevant_retained |")
    print("| -- | -- | -- |")
    for p in points:
        mark = "  <- recommended" if rec is not None and p.threshold == rec.threshold else ""
        print(
            "| %.1f | %.3f | %.3f |%s"
            % (p.threshold, p.irrelevant_filtered, p.relevant_retained, mark)
        )
    if rec is None:
        print("\nno threshold meets the retention floor")
    else:
        print(
            "\nrecommended: threshold=%.1f filters %.1f%% irrelevant, keeps %.1f%% relevant"
            % (rec.threshold, rec.irrelevant_filtered * 100, rec.relevant_retained * 100)
        )


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv or sys.argv[1:])
    setup_logging(args.log_level)
    samples = _load_samples(args.samples)
    thresholds = (
        [float(t) for t in args.thresholds.split(",") if t.strip()]
        if args.thresholds
        else _default_thresholds()
    )
    points = sweep(samples, thresholds)
    rec = recommend(points, min_retained=args.min_retained)
    _print_curve(points, rec)

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        with args.out.open("w") as fh:
            for p in points:
                fh.write(p.model_dump_json())
                fh.write("\n")
        log.info("wrote %d sweep points to %s", len(points), args.out)

    print(
        json.dumps(
            {
                "n_samples": len(samples),
                "recommended_threshold": rec.threshold if rec else None,
                "min_retained": args.min_retained,
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
