"""Shared CLI scaffolding for the surface runners.

Each runner defines a few callables (load cases, drive one trial, derive
the surface label) and calls ``run_eval(...)``. This collapses the
parse → resolve-models → fan-out → write → push pipeline that used to be
duplicated across the three ``run.py`` modules.

Concurrency: ``run_concurrent`` uses a ``ThreadPoolExecutor`` with a
copied ``contextvars.Context`` per worker, so the per-(provider, model)
override installed by ``use_model`` is thread-local. Judge calls nested
inside a trial are unaffected — they push/pop their own contextvar
inside the calling worker's context.

Determinism: per-task results are sorted on return so the JSONL output
ordering is independent of executor scheduling.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import contextlib
import contextvars
import logging
import time
from collections.abc import Callable
from contextlib import contextmanager
from pathlib import Path
from typing import ContextManager, Generator, TypeVar

from evals._llm_override import configured_models, use_model
from evals._metadata import git_sha_for, new_eval_run_id, utc_iso_now
from evals.schema import CaseResult, ScorerOutcome, Surface


log = logging.getLogger(__name__)

T = TypeVar("T")  # surface-specific case / scenario record


def add_common_args(parser: argparse.ArgumentParser, *, default_models: str = "") -> None:
    """Add the flags every runner shares.

    Surface-specific flags (``--cases``, ``--scenarios``, etc.) are
    declared by each runner before calling this so help output keeps the
    surface-specific args at the top.
    """
    parser.add_argument(
        "--models",
        default=default_models,
        help="Comma-separated model ids (provider inferred from prefix)",
    )
    parser.add_argument("--out", type=Path, default=None, help="JSONL output path")
    parser.add_argument(
        "--braintrust",
        default=None,
        help="Push to a Braintrust experiment of this base name",
    )
    parser.add_argument(
        "--dataset",
        default=None,
        help=(
            "Name of a Braintrust dataset to push the case set to (and link the experiment to). "
            "If unset, the experiment is pushed standalone. Set to enable per-row regression view "
            "across runs in the BT UI."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Use the surface's stub LLM (no API keys needed)",
    )
    parser.add_argument(
        "--runs",
        type=int,
        default=3,
        help="Trials per (case, model) for variance + bootstrap CI",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=8,
        help="Max in-flight LLM calls. Lower if you hit provider rate limits.",
    )
    parser.add_argument("--limit", type=int, default=None, help="Run only the first N cases")
    parser.add_argument(
        "--case-id",
        default=None,
        help="Run only the case with this id (debugging)",
    )
    parser.add_argument("--log-level", default="INFO", help="DEBUG / INFO / WARNING / ERROR")


def resolve_runnable(models_csv: str, *, dry_run: bool) -> tuple[list[tuple[str, str]], list[str]]:
    """Map a CSV of model ids to ``[(provider, model), ...]`` filtered by configured keys.

    Returns ``(runnable, skipped)``. Dry-run treats every requested model
    as runnable under the ``stub`` pseudo-provider so the same CLI shape
    works without API keys.
    """
    requested = [m.strip() for m in models_csv.split(",") if m.strip()]
    if dry_run:
        return [("stub", m) for m in requested], []
    runnable = configured_models(requested)
    runnable_set = {m for _, m in runnable}
    skipped = [m for m in requested if m not in runnable_set]
    for s in skipped:
        log.warning("skipping model %s — no provider/key configured", s)
    return runnable, skipped


def build_metadata(harness_file: Path, dataset_path: Path) -> dict[str, str]:
    """Capture reproducibility fields stamped onto every ``CaseResult``."""
    return {
        "eval_run_id": new_eval_run_id(),
        "run_timestamp": utc_iso_now(),
        "harness_git_sha": git_sha_for(harness_file),
        "dataset_git_sha": git_sha_for(dataset_path),
    }


# A trial returns (surface, expected_class, actual_class, raw_output, scorers).
# ``surface`` is per-trial so the wiki_updater runner can route
# process_instruction vs reconcile_document rows from a mixed dataset.
TrialResult = tuple[Surface, str, str, str, list[ScorerOutcome]]
RunOne = Callable[[T, str, str, int], TrialResult]


@contextmanager
def _empty_context() -> Generator[None]:
    yield


def run_concurrent(
    cases: list[T],
    *,
    runnable: list[tuple[str, str]],
    runs: int,
    run_one: RunOne[T],
    case_id: Callable[[T], str],
    metadata: dict[str, str],
    judge_models: list[str],
    concurrency: int,
    dry_run_ctx: ContextManager[None] | None = None,
) -> list[CaseResult]:
    """Fan every (case, provider/model, run_index) tuple out across a thread pool.

    ``dry_run_ctx`` (if any) is entered ONCE around the whole pool —
    surface stubs patch the module-global ``app.llm.client.complete``,
    so per-worker entry/exit would race. Live runs enter ``use_model``
    per-worker because the override is a ``ContextVar`` (thread-local
    via copied context).

    Errors raised by ``run_one`` are captured and stored on the result;
    one failing trial does not stop the run.
    """
    tasks: list[tuple[T, str, str, int]] = []
    for case in cases:
        for provider, model in runnable:
            for run_index in range(runs):
                tasks.append((case, provider, model, run_index))

    def _worker(case: T, provider: str, model: str, run_index: int) -> CaseResult:
        live_ctx: ContextManager[None] = (
            _empty_context() if dry_run_ctx is not None else use_model(provider, model)
        )
        start = time.monotonic()
        error = ""
        surface: Surface = "process_instruction"
        expected_class = actual_class = raw = ""
        scorers_out: list[ScorerOutcome] = []
        with live_ctx:
            try:
                surface, expected_class, actual_class, raw, scorers_out = run_one(
                    case, provider, model, run_index
                )
            except Exception as exc:
                error = repr(exc)
                log.warning(
                    "case=%s run=%d model=%s failed: %s",
                    case_id(case),
                    run_index,
                    model,
                    exc,
                )
        return CaseResult(
            case_id=case_id(case),
            surface=surface,
            provider=provider,
            model=model,
            run_index=run_index,
            expected_class=expected_class,
            actual_class=actual_class,
            raw_output=raw,
            scorers=scorers_out,
            error=error,
            latency_ms=int((time.monotonic() - start) * 1000),
            eval_run_id=metadata["eval_run_id"],
            run_timestamp=metadata["run_timestamp"],
            harness_git_sha=metadata["harness_git_sha"],
            dataset_git_sha=metadata["dataset_git_sha"],
            judge_models=judge_models,
        )

    outer = dry_run_ctx if dry_run_ctx is not None else contextlib.nullcontext()
    results: list[CaseResult] = []
    with outer, concurrent.futures.ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
        # Each submit gets its OWN context copy. ``Context.run`` is
        # single-entry — a shared context would raise "cannot enter
        # context: already entered" on the second concurrent task.
        futures = [pool.submit(contextvars.copy_context().run, _worker, *t) for t in tasks]
        for fut in concurrent.futures.as_completed(futures):
            results.append(fut.result())

    results.sort(key=lambda r: (r.case_id, r.provider, r.model, r.run_index))
    return results
