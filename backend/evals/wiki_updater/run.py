"""CLI: run the wiki_updater eval across a model matrix.

    cd backend
    uv run python -m evals.wiki_updater.run \\
        --cases evals/datasets/wiki_updater/cases.jsonl \\
        --models claude-sonnet-4-6,claude-opus-4-7,gpt-5,gemini-2.5-pro

See ``backend/evals/README.md`` for the full flag set and how the
provider/model selection works.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path
from typing import ContextManager, Iterator

from app.llm.agents import wiki_updater
from app.llm.agents.wiki_updater import IRRELEVANT_SENTINEL
from app.utils.logging import setup_logging

from evals import reporting, scorers
from evals._dry_run import stub_completions
from evals._llm_override import configured_models, use_model
from evals.schema import CaseResult, ScorerOutcome, TriggerClass, WikiUpdaterCase


log = logging.getLogger(__name__)


def _load_cases(path: Path, *, case_id: str | None, limit: int | None) -> list[WikiUpdaterCase]:
    rows: list[WikiUpdaterCase] = []
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

    Both ``process_instruction`` and ``reconcile_document`` return ``None``
    for NO_CHANGE. Only ``reconcile_document`` can return the IRRELEVANT
    sentinel — for the MCP surface, an IRRELEVANT-looking response is
    actually a CHANGE because the agent doesn't model that decision.
    """
    if raw is None:
        return TriggerClass.NO_CHANGE
    if surface == "reconcile_document" and raw == IRRELEVANT_SENTINEL:
        return TriggerClass.IRRELEVANT
    return TriggerClass.CHANGE


def _invoke_agent(case: WikiUpdaterCase) -> tuple[str | None, str]:
    """Drive the right agent function for ``case``.

    Returns ``(raw_return, normalized_raw_text)``. The second value is what
    gets stored in ``CaseResult.raw_output`` — same content but always a
    string (None → "<NO_CHANGE>", IRRELEVANT sentinel → "<IRRELEVANT>").
    """
    if case.surface == "process_instruction":
        payload = case.payload or {}
        raw = wiki_updater.process_instruction(
            wiki_path=case.wiki_path,
            current_body=case.current_body,
            payload=payload,
            source=case.source,
        )
    else:
        raw = wiki_updater.reconcile_document(
            wiki_path=case.wiki_path,
            current_body=case.current_body,
            source=case.source,
            title=case.doc_title,
            url=case.doc_url,
            content=case.doc_content or "",
        )
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
    judge_model: str | None,
) -> list[ScorerOutcome]:
    out: list[ScorerOutcome] = [scorers.trigger_class_match(case.expected_class, actual)]
    if actual is not TriggerClass.CHANGE:
        return out
    if raw is None or raw == IRRELEVANT_SENTINEL:
        return out  # defensive — shouldn't happen, classify guards
    new_body = raw
    out.append(scorers.bloat_ratio(case.current_body, new_body, max_ratio=case.max_bloat_ratio))
    out.append(scorers.markdown_valid(new_body))
    out.append(
        scorers.facts_present(new_body, case.expected_facts_present, judge_model=judge_model)
    )
    out.append(
        scorers.facts_preserved(new_body, case.expected_facts_preserved, judge_model=judge_model)
    )
    return out


def _run_one_model(
    cases: list[WikiUpdaterCase],
    *,
    provider: str,
    model: str,
    judge_model: str | None,
) -> Iterator[CaseResult]:
    for case in cases:
        start = time.monotonic()
        error = ""
        raw: str | None = None
        normalized_raw = ""
        try:
            raw, normalized_raw = _invoke_agent(case)
        except Exception as exc:
            error = repr(exc)
            log.warning("case %s failed against %s: %s", case.id, model, exc)
        actual = _classify(case.surface, raw)
        score_rows = _score_case(case, raw=raw, actual=actual, judge_model=judge_model)
        yield CaseResult(
            case_id=case.id,
            surface=case.surface,  # type: ignore[arg-type]
            provider=provider,
            model=model,
            expected_class=case.expected_class.value,
            actual_class=actual.value,
            raw_output=normalized_raw,
            scorers=score_rows,
            error=error,
            latency_ms=int((time.monotonic() - start) * 1000),
        )


def _resolve_context(
    provider: str, model: str, dry_run: bool, cases: list[WikiUpdaterCase]
) -> ContextManager[None]:
    if dry_run:
        return stub_completions(cases)
    return use_model(provider, model)


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Run the wiki_updater eval.")
    p.add_argument("--cases", type=Path, required=True, help="Path to cases.jsonl")
    p.add_argument(
        "--models",
        default="claude-sonnet-4-6",
        help="Comma-separated model ids (provider is inferred from prefix)",
    )
    p.add_argument(
        "--out",
        type=Path,
        default=None,
        help="JSONL output path. Default: runs/wiki_updater_<unix>.jsonl",
    )
    p.add_argument(
        "--judge-model",
        default=None,
        help="Model id for LLM-judge scorers. Defaults to the model under test.",
    )
    p.add_argument(
        "--braintrust",
        default=None,
        help="If set, also push results to this Braintrust experiment name.",
    )
    p.add_argument("--dry-run", action="store_true", help="Use the stub LLM (no API keys needed)")
    p.add_argument("--limit", type=int, default=None, help="Run only the first N cases")
    p.add_argument(
        "--case-id",
        default=None,
        help="Run only the case with this id (for debugging a single row)",
    )
    p.add_argument(
        "--log-level",
        default="INFO",
        help="Logging level (DEBUG/INFO/WARNING)",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv or sys.argv[1:])
    setup_logging(args.log_level)
    cases = _load_cases(args.cases, case_id=args.case_id, limit=args.limit)
    requested_models = [m.strip() for m in args.models.split(",") if m.strip()]

    if args.dry_run:
        runnable = [("stub", m) for m in requested_models]
        skipped: list[str] = []
    else:
        runnable = configured_models(requested_models)
        runnable_set = {m for _, m in runnable}
        skipped = [m for m in requested_models if m not in runnable_set]
        for s in skipped:
            log.warning("skipping model %s — no provider/key configured", s)

    if not runnable:
        log.error("no runnable models — set EVAL_*_API_KEY or pass --dry-run")
        return 2

    all_results: list[CaseResult] = []
    for provider, model in runnable:
        log.info("running %d cases against %s/%s", len(cases), provider, model)
        ctx = _resolve_context(provider, model, args.dry_run, cases)
        with ctx:
            for r in _run_one_model(
                cases,
                provider=provider,
                model=model,
                judge_model=args.judge_model or model,
            ):
                all_results.append(r)

    out_path = args.out or Path("runs") / ("wiki_updater_%d.jsonl" % int(time.time()))
    reporting.write_jsonl(out_path, all_results)
    # One table per surface — the dataset mixes process_instruction and reconcile_document.
    for surface in sorted({r.surface for r in all_results}):
        subset = [r for r in all_results if r.surface == surface]
        sub_summary = reporting.summarize(subset, surface=surface)  # type: ignore[arg-type]
        reporting.print_summary(sub_summary)
    if args.braintrust:
        reporting.push_to_braintrust(args.braintrust, all_results)
    print(
        json.dumps(
            {
                "out": str(out_path),
                "skipped_models": skipped,
                "models": sorted({r.model for r in all_results}),
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
