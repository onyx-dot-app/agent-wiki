"""CLI: run the ingest_selector eval across a model matrix.

    cd backend
    uv run python -m evals.ingest_selector.run \\
        --cases evals/datasets/ingest_selector/cases.jsonl \\
        --models claude-haiku-4-5,gpt-5-mini

Selector is a cheap/fast model in production — typical matrix is the
cheaper tier of each provider. See ``backend/evals/README.md`` for the
full flag set.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from collections.abc import Generator, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, ContextManager

from app.db.fts import SearchHit
from app.ingest.models import WikiUpdateCandidate
from app.llm import client as llm_client
from app.llm.agents.ingest_selector import select_candidates
from app.llm.client import CompletionResult
from app.utils.logging import setup_logging

from evals import reporting, scorers
from evals._llm_override import configured_models, use_model
from evals.schema import CaseResult, IngestSelectorCase


log = logging.getLogger(__name__)


def _load_cases(path: Path, *, case_id: str | None, limit: int | None) -> list[IngestSelectorCase]:
    rows: list[IngestSelectorCase] = []
    with path.open() as fh:
        for line_num, line in enumerate(fh, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(IngestSelectorCase.model_validate_json(line))
            except Exception as exc:
                raise ValueError("invalid case on line %d: %s" % (line_num, exc)) from exc
    if case_id:
        rows = [c for c in rows if c.id == case_id]
    if limit is not None:
        rows = rows[:limit]
    if not rows:
        raise ValueError("no cases loaded from %s (id-filter=%s)" % (path, case_id))
    return rows


def _to_wiki_update_candidates(case: IngestSelectorCase) -> list[WikiUpdateCandidate]:
    return [
        WikiUpdateCandidate(
            hit=SearchHit(
                doc_id=c.path,
                path=c.path,
                title=None,
                snippet=c.body[:120],
                score=1.0,
            ),
            body=c.body,
        )
        for c in case.candidates
    ]


def _invoke_selector(case: IngestSelectorCase, *, model: str) -> list[str]:
    candidates = _to_wiki_update_candidates(case)
    kept = select_candidates(
        title=case.doc_title,
        content=case.doc_content,
        candidates=candidates,
        model=model,
    )
    return [k.hit.path for k in kept]


@contextmanager
def _stub_selector(cases: list[IngestSelectorCase]) -> Generator[None]:
    """Patch ``client.complete`` to return the labeled relevant set as a JSON list.

    Selector expects a JSON list of 1-indexed batch positions. We match the
    case by the set of candidate paths embedded in the rendered prompt, then
    emit ``[i+1, ...]`` for whichever batch positions correspond to the
    labeled relevant paths.
    """
    original = llm_client.complete
    seen_keys: dict[tuple[str, ...], str] = {}
    case_meta: list[tuple[tuple[str, ...], IngestSelectorCase]] = []
    for case in cases:
        paths = tuple(c.path for c in case.candidates)
        if paths and paths in seen_keys and seen_keys[paths] != case.id:
            raise ValueError(
                "selector stub collision: cases %s and %s share the same candidate paths"
                % (seen_keys[paths], case.id)
            )
        if paths:
            seen_keys[paths] = case.id
        case_meta.append((paths, case))

    def _stub(
        messages: list[dict[str, Any]],
        *,
        model: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int = llm_client.DEFAULT_MAX_TOKENS,
    ) -> CompletionResult:
        del model, tools, max_tokens
        user_text = "\n".join(m.get("content", "") for m in messages if m.get("role") == "user")
        # Find the case whose candidate paths all appear in the prompt. When
        # multiple cases qualify (sel-03's paths are a subset of sel-05's,
        # for example), prefer the one with the most candidates so subset
        # collisions don't route the wrong response.
        matched: IngestSelectorCase | None = None
        best_size = -1
        for paths, case in case_meta:
            if paths and all(p in user_text for p in paths) and len(paths) > best_size:
                matched = case
                best_size = len(paths)
        if matched is None:
            return CompletionResult(text="[]")
        # Reconstruct batch order from the prompt text — find paths in
        # the order they appear, then emit 1-indexed positions of those
        # that are in expected_kept_paths.
        order = sorted(
            (user_text.index(c.path), c.path) for c in matched.candidates if c.path in user_text
        )
        ordered_paths = [p for _, p in order]
        expected_set = set(matched.expected_kept_paths)
        indices = [i + 1 for i, p in enumerate(ordered_paths) if p in expected_set]
        return CompletionResult(text=json.dumps(indices))

    llm_client.complete = _stub  # type: ignore[assignment]
    try:
        yield
    finally:
        llm_client.complete = original  # type: ignore[assignment]


def _run_one_model(
    cases: list[IngestSelectorCase],
    *,
    provider: str,
    model: str,
    runs: int,
) -> Iterator[CaseResult]:
    for case in cases:
        for run_index in range(runs):
            start = time.monotonic()
            error = ""
            kept_paths: list[str] = []
            try:
                kept_paths = _invoke_selector(case, model=model)
            except Exception as exc:
                error = repr(exc)
                log.warning(
                    "selector case %s run %d failed against %s: %s",
                    case.id,
                    run_index,
                    model,
                    exc,
                )
            precision, recall, f1 = scorers.selector_set_metrics(
                case.expected_kept_paths, kept_paths
            )
            yield CaseResult(
                case_id=case.id,
                surface="ingest_selector",
                provider=provider,
                model=model,
                run_index=run_index,
                expected_class=",".join(sorted(case.expected_kept_paths)) or "<none>",
                actual_class=",".join(sorted(kept_paths)) or "<none>",
                raw_output=json.dumps(kept_paths),
                scorers=[precision, recall, f1],
                error=error,
                latency_ms=int((time.monotonic() - start) * 1000),
            )


def _resolve_context(
    provider: str, model: str, dry_run: bool, cases: list[IngestSelectorCase]
) -> ContextManager[None]:
    if dry_run:
        return _stub_selector(cases)
    return use_model(provider, model)


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Run the ingest_selector eval.")
    p.add_argument("--cases", type=Path, required=True)
    p.add_argument("--models", default="claude-haiku-4-5")
    p.add_argument("--out", type=Path, default=None)
    p.add_argument("--braintrust", default=None)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--runs", type=int, default=3, help="Trials per (case, model) for variance")
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--case-id", default=None)
    p.add_argument("--log-level", default="INFO")
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
        log.info("running %d selector cases against %s/%s", len(cases), provider, model)
        ctx = _resolve_context(provider, model, args.dry_run, cases)
        with ctx:
            for r in _run_one_model(cases, provider=provider, model=model, runs=args.runs):
                all_results.append(r)

    out_path = args.out or Path("runs") / ("ingest_selector_%d.jsonl" % int(time.time()))
    reporting.write_jsonl(out_path, all_results)
    summary = reporting.summarize(all_results, surface="ingest_selector")
    reporting.print_summary(summary)
    if args.braintrust:
        reporting.push_to_braintrust(args.braintrust, all_results)
    print(json.dumps({"out": str(out_path), "skipped_models": skipped}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
