"""Unit coverage for the ingest BM25 pre-filter sweep."""

from __future__ import annotations

from pathlib import Path

from evals.ingest_selector.sweep import recommend, sweep
from evals.schema import RetrievalSample


SAMPLES = (
    Path(__file__).resolve().parent.parent
    / "datasets"
    / "ingest_selector"
    / "retrieval_samples.jsonl"
)


def _s(score: float, relevant: bool, sid: str) -> RetrievalSample:
    return RetrievalSample(id=sid, wiki_path="p/%s.md" % sid, bm25_score=score, relevant=relevant)


def test_seed_dataset_loads_and_has_both_classes() -> None:
    rows = [
        RetrievalSample.model_validate_json(line)
        for line in SAMPLES.read_text().splitlines()
        if line.strip()
    ]
    assert len(rows) >= 40
    assert any(r.relevant for r in rows)
    assert any(not r.relevant for r in rows)


def test_sweep_monotonic() -> None:
    """Higher threshold filters >= and retains <= — both monotonic."""
    samples = [
        _s(10, True, "r1"),
        _s(30, True, "r2"),
        _s(5, False, "i1"),
        _s(25, False, "i2"),
    ]
    pts = sweep(samples, [0.0, 20.0, 40.0])
    filtered = [p.irrelevant_filtered for p in pts]
    retained = [p.relevant_retained for p in pts]
    assert filtered == sorted(filtered)  # non-decreasing
    assert retained == sorted(retained, reverse=True)  # non-increasing


def test_sweep_exact_fractions() -> None:
    samples = [
        _s(10, True, "r1"),
        _s(30, True, "r2"),  # relevant: 10, 30
        _s(5, False, "i1"),
        _s(25, False, "i2"),  # irrelevant: 5, 25
    ]
    [p20] = sweep(samples, [20.0])
    # keep score>=20: relevant kept r2 (30) → 1/2=0.5; irrelevant kept i2
    # (25), filtered i1 (5) → 1/2=0.5
    assert p20.relevant_retained == 0.5
    assert p20.irrelevant_filtered == 0.5


def test_recommend_picks_highest_threshold_meeting_floor() -> None:
    samples = [_s(10, True, "r1"), _s(30, True, "r2"), _s(5, False, "i1")]
    pts = sweep(samples, [0.0, 10.0, 20.0, 40.0])
    # retained: t0=1.0, t10=1.0, t20=0.5, t40=0.0 → floor 0.95 met at 0 and 10
    rec = recommend(pts, min_retained=0.95)
    assert rec is not None
    assert rec.threshold == 10.0


def test_recommend_none_when_floor_unreachable() -> None:
    samples = [_s(10, True, "r1"), _s(5, False, "i1")]
    pts = sweep(samples, [20.0, 40.0])  # both drop the only relevant sample
    assert recommend(pts, min_retained=0.95) is None
