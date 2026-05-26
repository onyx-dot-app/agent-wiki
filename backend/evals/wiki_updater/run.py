"""CLI: run the wiki_updater eval across a model matrix.

    cd backend
    uv run python -m evals.wiki_updater.run \\
        --cases evals/datasets/wiki_updater/cases.jsonl \\
        --models claude-sonnet-4-6,gpt-5

The dataset is mixed: each case is either ``process_instruction``
(MCP write path) or ``reconcile_document`` (ingest write path via
``batch_reconcile``). The runner produces one summary table + one
Braintrust experiment per surface so trigger trinaries stay comparable
across nightly runs.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

import yaml

from app.db.fts import SearchHit
from app.ingest.models import WikiUpdateCandidate
from app.llm.agents.common import IRRELEVANT_SENTINEL
from app.llm.agents.ingest_batch_reconciler import batch_reconcile
from app.llm.agents.nl_updater import process_instruction
from app.utils.logging import setup_logging

from evals import _cli, reporting, scorers
from evals._dry_run import stub_completions
from evals.schema import ScorerOutcome, Surface, TriggerClass, WikiUpdaterCase


log = logging.getLogger(__name__)


def _load_cases(path: Path, *, case_id: str | None, limit: int | None) -> list[WikiUpdaterCase]:
    """Load cases from either a directory of YAML files (one per case) or a JSONL file.

    Per-case YAML matches the layout external_agent uses for scenarios:
    each file at ``evals/datasets/wiki_updater/cases/<id>.yaml`` is
    independently editable and diff-friendly. JSONL is still accepted for
    backward compatibility and ad-hoc machine-generated slices.
    """
    rows: list[WikiUpdaterCase] = []
    if path.is_dir():
        for yaml_path in sorted(path.glob("*.yaml")):
            try:
                rows.append(WikiUpdaterCase.model_validate(yaml.safe_load(yaml_path.read_text())))
            except Exception as exc:
                raise ValueError("invalid case in %s: %s" % (yaml_path, exc)) from exc
    else:
        with path.open() as fh:
            for line_num, line in enumerate(fh, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(WikiUpdaterCase.model_validate_json(line))
                except Exception as exc:
                    raise ValueError("invalid case on line %d: %s" % (line_num, exc)) from exc
    if case_id:
        rows = [c for c in rows if c.id == case_id]
    if limit is not None:
        rows = rows[:limit]
    if not rows:
        raise ValueError("no cases loaded from %s (id-filter=%s)" % (path, case_id))
    return rows


def _classify(surface: str, raw: str | None) -> TriggerClass:
    """Map an agent return value to a trigger class.

    Both surfaces return ``None`` for NO_CHANGE; only ``reconcile_document``
    can return the IRRELEVANT sentinel — for MCP, an IRRELEVANT-looking
    return is actually CHANGE because the agent doesn't model that
    decision.
    """
    if raw is None:
        return TriggerClass.NO_CHANGE
    if surface == "reconcile_document" and raw == IRRELEVANT_SENTINEL:
        return TriggerClass.IRRELEVANT
    return TriggerClass.CHANGE


def _invoke_agent(case: WikiUpdaterCase, *, model: str) -> tuple[str | None, str]:
    """Drive the right agent function. Returns ``(raw, normalized_raw_text)``."""
    if case.surface == "process_instruction":
        payload = case.payload or {}
        raw = process_instruction(
            wiki_path=case.wiki_path,
            current_body=case.current_body,
            payload=payload,
            source=case.source,
        )
    else:
        candidate = WikiUpdateCandidate(
            hit=SearchHit(
                doc_id=case.wiki_path,
                path=case.wiki_path,
                title=None,
                snippet=case.current_body[:120],
                score=1.0,
            ),
            body=case.current_body,
        )
        batch = batch_reconcile(
            title=case.doc_title,
            url=case.doc_url or "",
            content=case.doc_content or "",
            source=case.source,
            candidates=[candidate],
            model=model,
        )
        raw = batch.results[0] if batch.results else None
    if raw is None:
        return raw, "<NO_CHANGE>"
    if raw == IRRELEVANT_SENTINEL:
        return raw, "<IRRELEVANT>"
    return raw, raw


def _score_case(
    case: WikiUpdaterCase,
    *,
    raw: str | None,
    actual: TriggerClass,
    judge_models: tuple[str, ...] | None,
) -> list[ScorerOutcome]:
    out: list[ScorerOutcome] = [scorers.trigger_class_match(case.expected_class, actual)]
    if actual is not TriggerClass.CHANGE:
        return out
    if raw is None or raw == IRRELEVANT_SENTINEL:
        return out
    new_body = raw
    out.append(scorers.bloat_ratio(case.current_body, new_body, max_ratio=case.max_bloat_ratio))
    out.append(scorers.diff_addition_ratio(case.current_body, new_body))
    out.append(scorers.entity_density_delta(case.current_body, new_body))
    out.append(scorers.markdown_valid(new_body))
    out.append(
        scorers.facts_present(new_body, case.expected_facts_present, judge_models=judge_models)
    )
    out.append(
        scorers.facts_preserved(new_body, case.expected_facts_preserved, judge_models=judge_models)
    )
    return out


def _make_run_one(
    judge_models: tuple[str, ...] | None,
) -> _cli.RunOne[WikiUpdaterCase]:
    def _run_one(
        case: WikiUpdaterCase, provider: str, model: str, run_index: int
    ) -> tuple[Surface, str, str, str, list[ScorerOutcome]]:
        del provider, run_index
        raw, normalized = _invoke_agent(case, model=model)
        actual = _classify(case.surface, raw)
        rows = _score_case(case, raw=raw, actual=actual, judge_models=judge_models)
        return (
            case.surface,
            case.expected_class.value,
            actual.value,
            normalized,
            rows,
        )

    return _run_one


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Run the wiki_updater eval.")
    p.add_argument("--cases", type=Path, required=True, help="Path to cases.jsonl")
    p.add_argument(
        "--judge-models",
        default=",".join(scorers.DEFAULT_JUDGE_PANEL),
        help="Comma-separated judge model panel for facts_present/preserved.",
    )
    _cli.add_common_args(p, default_models="claude-sonnet-4-6")
    return p.parse_args(argv)


_PROD_TAGS = {"real-prod-commit", "real-prod-decision"}


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv or sys.argv[1:])
    setup_logging(args.log_level)
    cases = _load_cases(args.cases, case_id=args.case_id, limit=args.limit)
    if args.dry_run:
        # Prod-mined cases share wiki bodies across many revisions; the
        # dry-run stub can't tell them apart by 200-char prefix, so
        # ci_assert_baseline would flag the resulting wrong-class trials.
        # Live runs are unaffected (each case carries a unique full body).
        before = len(cases)
        cases = [c for c in cases if not _PROD_TAGS.intersection(c.tags or [])]
        dropped = before - len(cases)
        if dropped:
            log.info(
                "dry-run: skipping %d prod-mined cases (curated cases provide the smoke signal)",
                dropped,
            )
    runnable, skipped = _cli.resolve_runnable(args.models, dry_run=args.dry_run)
    if not runnable:
        log.error("no runnable models — set EVAL_*_API_KEY or pass --dry-run")
        return 2
    metadata = _cli.build_metadata(Path(__file__), args.cases)
    judge_panel = tuple(j.strip() for j in args.judge_models.split(",") if j.strip())

    log.info(
        "running %d cases × %d models × %d runs (concurrency=%d)",
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
        dry_run_ctx=stub_completions(cases) if args.dry_run else None,
    )

    out_path = args.out or Path("runs") / ("wiki_updater_%d.jsonl" % int(time.time()))
    reporting.write_jsonl(out_path, results)
    # Push the case set as a BT dataset once (per surface) before experiments,
    # so experiments can link results to dataset rows for per-row regression view.
    dataset_name_by_surface: dict[str, str] = {}
    if args.dataset:
        for surface in sorted({c.surface for c in cases}):
            ds = "%s-%s" % (args.dataset, surface.replace("_", "-"))
            subset_cases = [c for c in cases if c.surface == surface]
            reporting.push_wiki_updater_dataset(ds, subset_cases)
            dataset_name_by_surface[surface] = ds
    surface_urls: dict[str, str] = {}
    for surface in sorted({r.surface for r in results}):
        subset = [r for r in results if r.surface == surface]
        sub_summary = reporting.summarize(subset, surface=surface)  # type: ignore[arg-type]
        reporting.print_summary(sub_summary)
        bt_url = ""
        if args.braintrust:
            experiment = "%s-%s" % (args.braintrust, surface.replace("_", "-"))
            bt_url = reporting.push_to_braintrust(
                experiment, subset, dataset=dataset_name_by_surface.get(surface)
            )
            surface_urls[surface] = bt_url
        reporting.write_github_summary(sub_summary, braintrust_url=bt_url)
    print(
        json.dumps(
            {
                "out": str(out_path),
                "skipped_models": skipped,
                "models": sorted({r.model for r in results}),
                "braintrust_urls": surface_urls,
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
