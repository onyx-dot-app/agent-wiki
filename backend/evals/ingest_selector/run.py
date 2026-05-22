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
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from app.db.fts import SearchHit
from app.ingest.models import WikiUpdateCandidate
from app.llm import client as llm_client
from app.llm.agents.ingest_selector import select_candidates
from app.llm.client import CompletionResult
from app.utils.logging import setup_logging

from evals import _cli, reporting, scorers
from evals.schema import IngestSelectorCase, ScorerOutcome, Surface


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
        # Multi-match: pick the case with the most candidate paths in the
        # prompt so subset-overlaps (sel-03 paths ⊂ sel-05) route correctly.
        matched: IngestSelectorCase | None = None
        best_size = -1
        for paths, case in case_meta:
            if paths and all(p in user_text for p in paths) and len(paths) > best_size:
                matched = case
                best_size = len(paths)
        if matched is None:
            return CompletionResult(text="[]")
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


def _run_one(
    case: IngestSelectorCase, provider: str, model: str, run_index: int
) -> tuple[Surface, str, str, str, list[ScorerOutcome]]:
    del provider, run_index
    candidates = _to_wiki_update_candidates(case)
    kept = select_candidates(
        title=case.doc_title,
        content=case.doc_content,
        candidates=candidates,
        model=model,
    )
    kept_paths = [k.hit.path for k in kept]
    p, r, f1 = scorers.selector_set_metrics(case.expected_kept_paths, kept_paths)
    return (
        "ingest_selector",
        ",".join(sorted(case.expected_kept_paths)) or "<none>",
        ",".join(sorted(kept_paths)) or "<none>",
        json.dumps(kept_paths),
        [p, r, f1],
    )


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Run the ingest_selector eval.")
    p.add_argument("--cases", type=Path, required=True)
    _cli.add_common_args(p, default_models="claude-haiku-4-5,gpt-5-mini")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv or sys.argv[1:])
    setup_logging(args.log_level)
    cases = _load_cases(args.cases, case_id=args.case_id, limit=args.limit)
    runnable, skipped = _cli.resolve_runnable(args.models, dry_run=args.dry_run)
    if not runnable:
        log.error("no runnable models — set EVAL_*_API_KEY or pass --dry-run")
        return 2
    metadata = _cli.build_metadata(Path(__file__), args.cases)

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
        run_one=_run_one,
        case_id=lambda c: c.id,
        metadata=metadata,
        judge_models=[],
        concurrency=args.concurrency,
        dry_run_ctx=_stub_selector(cases) if args.dry_run else None,
    )

    out_path = args.out or Path("runs") / ("ingest_selector_%d.jsonl" % int(time.time()))
    reporting.write_jsonl(out_path, results)
    summary = reporting.summarize(results, surface="ingest_selector")
    reporting.print_summary(summary)
    bt_url = ""
    if args.braintrust:
        bt_url = reporting.push_to_braintrust(args.braintrust, results)
    reporting.write_github_summary(summary, braintrust_url=bt_url)
    print(json.dumps({"out": str(out_path), "skipped_models": skipped, "braintrust_url": bt_url}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
