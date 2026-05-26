"""CLI: run the external-agent eval across a model matrix.

    cd backend
    uv run python -m evals.external_agent.run \\
        --scenarios evals/datasets/external_agent/scenarios \\
        --models claude-sonnet-4-6,gpt-5

External-agent measures WHEN a Claude-Code-style agent decides to call
``update_doc_nl`` (precision/recall over the labeled update set) plus the
HOW quality of the resulting bodies (reuses the standard fact + bloat
scorers, averaged per scenario).
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
from evals.external_agent._stub import stub_external_agent
from evals.external_agent.harness import Scenario, WikiState, run_scenario
from evals.external_agent.harness import load_scenarios as _load_scenarios
from evals.schema import ScorerOutcome, Surface


log = logging.getLogger(__name__)


def _score_scenario(
    scenario: Scenario, state: WikiState, *, judge_models: tuple[str, ...] | None = None
) -> list[ScorerOutcome]:
    """Per-scenario scorer rows.

    Surface-level shape:

    * ``update_precision`` / ``update_recall`` / ``update_f1`` — set
      metrics over the paths the agent actually changed.
    * ``no_touch_compliance`` — fraction of ``expected_not_updated`` paths
      whose body is unchanged from the seed.
    * Quality scorers (facts_present/preserved/bloat/diff/entity-density)
      are computed per expected update against the final body and averaged.
    """
    expected_paths = [u.path for u in scenario.expected_updates]
    actual_paths = state.updated_paths()

    p_raw, r_raw, f1_raw = scorers.selector_set_metrics(expected_paths, actual_paths)
    precision = ScorerOutcome(
        name="update_precision", score=p_raw.score, passed=p_raw.passed, detail=p_raw.detail
    )
    recall = ScorerOutcome(
        name="update_recall", score=r_raw.score, passed=r_raw.passed, detail=r_raw.detail
    )
    f1 = ScorerOutcome(
        name="update_f1", score=f1_raw.score, passed=f1_raw.passed, detail=f1_raw.detail
    )

    if scenario.expected_not_updated:
        kept = sum(
            1
            for p in scenario.expected_not_updated
            if state.current_body(p) == state.original_body(p)
        )
        no_touch = ScorerOutcome(
            name="no_touch_compliance",
            score=kept / len(scenario.expected_not_updated),
            passed=kept == len(scenario.expected_not_updated),
            detail="kept=%d/%d" % (kept, len(scenario.expected_not_updated)),
        )
    else:
        no_touch = ScorerOutcome(
            name="no_touch_compliance", score=1.0, passed=True, detail="no constraints"
        )

    facts_present_scores: list[float] = []
    facts_preserved_scores: list[float] = []
    bloat_scores: list[float] = []
    diff_addition_scores: list[float] = []
    entity_density_scores: list[float] = []
    for upd in scenario.expected_updates:
        if upd.path not in actual_paths:
            facts_present_scores.append(0.0)
            facts_preserved_scores.append(0.0)
            continue
        body = state.current_body(upd.path)
        original = state.original_body(upd.path)
        fp = scorers.facts_present(body, upd.facts_present, judge_models=judge_models)
        fk = scorers.facts_preserved(body, upd.facts_preserved, judge_models=judge_models)
        br = scorers.bloat_ratio(original, body, max_ratio=upd.max_bloat_ratio)
        da = scorers.diff_addition_ratio(original, body)
        ed = scorers.entity_density_delta(original, body)
        facts_present_scores.append(fp.score)
        facts_preserved_scores.append(fk.score)
        bloat_scores.append(br.score)
        diff_addition_scores.append(da.score)
        entity_density_scores.append(ed.score)

    def _avg(xs: list[float]) -> float:
        return sum(xs) / len(xs) if xs else 1.0

    rows: list[ScorerOutcome] = [precision, recall, f1, no_touch]
    rows.append(
        ScorerOutcome(
            name="facts_present_avg",
            score=_avg(facts_present_scores),
            passed=_avg(facts_present_scores) >= 0.8,
            detail="n=%d" % len(facts_present_scores),
        )
    )
    rows.append(
        ScorerOutcome(
            name="facts_preserved_avg",
            score=_avg(facts_preserved_scores),
            passed=_avg(facts_preserved_scores) >= 0.9,
            detail="n=%d" % len(facts_preserved_scores),
        )
    )
    rows.append(
        ScorerOutcome(
            name="bloat_ratio_avg",
            score=_avg(bloat_scores) if bloat_scores else 1.0,
            passed=_avg(bloat_scores) >= 0.9 if bloat_scores else True,
            detail="n=%d" % len(bloat_scores),
        )
    )
    rows.append(
        ScorerOutcome(
            name="diff_addition_ratio_avg",
            score=_avg(diff_addition_scores) if diff_addition_scores else 1.0,
            passed=_avg(diff_addition_scores) >= 0.8 if diff_addition_scores else True,
            detail="n=%d" % len(diff_addition_scores),
        )
    )
    rows.append(
        ScorerOutcome(
            name="entity_density_delta_avg",
            score=_avg(entity_density_scores) if entity_density_scores else 1.0,
            passed=_avg(entity_density_scores) >= 0.8 if entity_density_scores else True,
            detail="n=%d" % len(entity_density_scores),
        )
    )
    return rows


def _make_run_one(
    judge_models: tuple[str, ...] | None,
) -> _cli.RunOne[Scenario]:
    def _run_one(
        scenario: Scenario, provider: str, model: str, run_index: int
    ) -> tuple[Surface, str, str, str, list[ScorerOutcome]]:
        del provider, run_index
        state = run_scenario(scenario, model=model)
        rows = _score_scenario(scenario, state, judge_models=judge_models)
        expected = ",".join(sorted(u.path for u in scenario.expected_updates)) or "<none>"
        actual = ",".join(sorted(state.updated_paths())) or "<none>"
        raw = json.dumps(
            {
                "update_calls": state.update_calls,
                "final_bodies": {p: state.current_body(p) for p in state.updated_paths()},
            }
        )
        return ("external_agent", expected, actual, raw, rows)

    return _run_one


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Run the external-agent eval.")
    p.add_argument(
        "--scenarios",
        type=Path,
        default=Path("evals/datasets/external_agent/scenarios"),
        help="Directory of scenario YAML files",
    )
    p.add_argument(
        "--judge-models",
        default=",".join(scorers.DEFAULT_JUDGE_PANEL),
        help="Comma-separated judge model panel for facts_present/preserved.",
    )
    p.add_argument(
        "--scenario-id",
        default=None,
        help="Run only the scenario with this id (alias for --case-id)",
    )
    _cli.add_common_args(p, default_models="claude-sonnet-4-6")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv or sys.argv[1:])
    setup_logging(args.log_level)
    scenarios = _load_scenarios(args.scenarios)
    target_id = args.scenario_id or args.case_id
    if target_id:
        scenarios = [s for s in scenarios if s.id == target_id]
        if not scenarios:
            log.error("no scenario with id %s", target_id)
            return 2
    if args.limit is not None:
        scenarios = scenarios[: args.limit]

    runnable, skipped = _cli.resolve_runnable(args.models, dry_run=args.dry_run)
    if not runnable:
        log.error("no runnable models — set EVAL_*_API_KEY or pass --dry-run")
        return 2
    metadata = _cli.build_metadata(Path(__file__), args.scenarios)
    judge_panel = tuple(j.strip() for j in args.judge_models.split(",") if j.strip())

    log.info(
        "running %d scenarios × %d models × %d runs (concurrency=%d)",
        len(scenarios),
        len(runnable),
        args.runs,
        args.concurrency,
    )
    results = _cli.run_concurrent(
        scenarios,
        runnable=runnable,
        runs=args.runs,
        run_one=_make_run_one(judge_panel),
        case_id=lambda s: s.id,
        metadata=metadata,
        judge_models=list(judge_panel),
        concurrency=args.concurrency,
        dry_run_ctx=stub_external_agent(scenarios) if args.dry_run else None,
    )

    out_path = args.out or Path("runs") / ("external_agent_%d.jsonl" % int(time.time()))
    reporting.write_jsonl(out_path, results)
    summary = reporting.summarize(results, surface="external_agent")
    reporting.print_summary(summary)
    bt_url = ""
    if args.braintrust:
        bt_url = reporting.push_to_braintrust(args.braintrust, results, dataset=args.dataset)
    reporting.write_github_summary(summary, braintrust_url=bt_url)
    print(json.dumps({"out": str(out_path), "skipped_models": skipped, "braintrust_url": bt_url}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
