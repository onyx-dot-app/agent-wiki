"""CLI: run the external-agent eval across a model matrix.

    cd backend
    uv run python -m evals.external_agent.run \\
        --scenarios evals/datasets/external_agent/scenarios \\
        --models claude-sonnet-4-6,claude-opus-4-7,gpt-5,gemini-2.5-pro

The dry-run mode skips the agent loop entirely and synthesizes a "perfect
oracle" run from the scenario's ``expected_updates``. Useful for harness
validation, not as a real signal — for real numbers, set API keys and
drop ``--dry-run``.
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

from app.llm import client as llm_client
from app.llm.agents.common import NO_CHANGE_SENTINEL
from app.llm.client import CompletionResult
from app.utils.logging import setup_logging

from evals import reporting, scorers
from evals._llm_override import configured_models, use_model
from evals.external_agent.harness import Scenario, WikiState, load_scenarios, run_scenario
from evals.schema import CaseResult, ScorerOutcome


log = logging.getLogger(__name__)

# How many leading chars of a doc body identify it inside the _complete stub.
# Matches the substring length used in the lookup loop below.
_BODY_FINGERPRINT_LEN = 120


def _score_scenario(
    scenario: Scenario, state: WikiState, *, judge_model: str | None = None
) -> list[ScorerOutcome]:
    """Produce the per-scenario scorer rows.

    Surface-level shape:

    * ``update_precision`` / ``update_recall`` / ``update_f1`` — set
      metrics over the paths the agent actually changed (vs the labeled
      ``expected_updates`` set).
    * ``no_touch_compliance`` — fraction of ``expected_not_updated`` paths
      whose body is unchanged from the seed.
    * Per expected update: ``facts_present`` and ``facts_preserved`` are
      computed against the final body. Aggregated as the mean across all
      expected updates so the per-scenario row stays a single value.
    """
    expected_paths = [u.path for u in scenario.expected_updates]
    actual_paths = state.updated_paths()

    precision, recall, f1 = scorers.selector_set_metrics(expected_paths, actual_paths)
    precision = ScorerOutcome(
        name="update_precision",
        score=precision.score,
        passed=precision.passed,
        detail=precision.detail,
    )
    recall = ScorerOutcome(
        name="update_recall", score=recall.score, passed=recall.passed, detail=recall.detail
    )
    f1 = ScorerOutcome(name="update_f1", score=f1.score, passed=f1.passed, detail=f1.detail)

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
            detail=f"kept={kept}/{len(scenario.expected_not_updated)}",
        )
    else:
        no_touch = ScorerOutcome(
            name="no_touch_compliance", score=1.0, passed=True, detail="no constraints"
        )

    # Quality scorers per expected update, then averaged.
    facts_present_scores: list[float] = []
    facts_preserved_scores: list[float] = []
    bloat_scores: list[float] = []
    for upd in scenario.expected_updates:
        if upd.path not in actual_paths:
            facts_present_scores.append(0.0)
            facts_preserved_scores.append(0.0)
            continue
        body = state.current_body(upd.path)
        fp = scorers.facts_present(body, upd.facts_present, judge_model=judge_model)
        fk = scorers.facts_preserved(body, upd.facts_preserved, judge_model=judge_model)
        br = scorers.bloat_ratio(state.original_body(upd.path), body, max_ratio=upd.max_bloat_ratio)
        facts_present_scores.append(fp.score)
        facts_preserved_scores.append(fk.score)
        bloat_scores.append(br.score)

    def _avg(xs: list[float]) -> float:
        return sum(xs) / len(xs) if xs else 1.0

    rows: list[ScorerOutcome] = [precision, recall, f1, no_touch]
    rows.append(
        ScorerOutcome(
            name="facts_present_avg",
            score=_avg(facts_present_scores),
            passed=_avg(facts_present_scores) >= 0.8,
            detail=f"n={len(facts_present_scores)}",
        )
    )
    rows.append(
        ScorerOutcome(
            name="facts_preserved_avg",
            score=_avg(facts_preserved_scores),
            passed=_avg(facts_preserved_scores) >= 0.9,
            detail=f"n={len(facts_preserved_scores)}",
        )
    )
    rows.append(
        ScorerOutcome(
            name="bloat_ratio_avg",
            score=_avg(bloat_scores) if bloat_scores else 1.0,
            passed=_avg(bloat_scores) >= 0.9 if bloat_scores else True,
            detail=f"n={len(bloat_scores)}",
        )
    )
    return rows


@contextmanager
def _stub_external_agent(scenarios: list[Scenario]) -> Generator[None]:
    """Patch ``client.stream`` to drive the agent loop with canned tool calls.

    For each scenario, the stub emits exactly one ``update_doc_nl`` tool
    call per ``expected_updates`` path, then a final text turn. The
    instruction string is synthesized from the expected facts so the
    real ``wiki_updater.process_instruction`` (also stub-mocked) returns
    a body that satisfies the quality scorers.

    Concretely: we also patch ``client.complete`` to emit a class-shaped
    response for ``process_instruction`` — same trick the wiki_updater
    dry-run uses, scoped to the harness so the agent sees correct
    behavior end-to-end.
    """
    scenario_by_prompt: dict[str, Scenario] = {}
    for scenario in scenarios:
        key = scenario.prompt.strip()
        existing = scenario_by_prompt.get(key)
        if existing is not None and existing.id != scenario.id:
            raise ValueError(
                "external-agent stub collision: scenarios %s and %s share the same prompt"
                % (existing.id, scenario.id)
            )
        scenario_by_prompt[key] = scenario

    # The _complete stub matches a wiki doc by `path` plus the first
    # _BODY_FINGERPRINT_LEN chars of its body and returns the body with the
    # matching scenario's expected_facts_present appended. Two scenarios that
    # share that key with DIFFERENT expected facts would silently route to
    # whichever is iterated first, masking real process_instruction behavior.
    # Sharing the same key with identical facts (or no expected_updates at all)
    # is fine — the canned response is the same either way.
    seen_fact_keys: dict[tuple[str, str], tuple[str, tuple[str, ...]]] = {}
    for scenario in scenarios:
        update_paths = {u.path for u in scenario.expected_updates}
        update_facts = {
            u.path: tuple(c.text for c in u.facts_present) for u in scenario.expected_updates
        }
        for d in scenario.wiki_state:
            if d.path not in update_paths:
                continue
            body_key = (d.path, d.body[:_BODY_FINGERPRINT_LEN])
            facts = update_facts[d.path]
            existing = seen_fact_keys.get(body_key)
            if existing is not None and existing[0] != scenario.id and existing[1] != facts:
                raise ValueError(
                    "external-agent stub _complete collision: scenarios %s and %s share doc "
                    "path %r with the same first %d-char body prefix but different "
                    "expected_facts_present — canned response would be ambiguous"
                    % (existing[0], scenario.id, d.path, _BODY_FINGERPRINT_LEN)
                )
            seen_fact_keys[body_key] = (scenario.id, facts)

    original_stream = llm_client.stream
    original_complete = llm_client.complete

    # State per-scenario: which expected updates have been emitted so far.
    cursor: dict[str, int] = {}

    def _stream(
        messages: list[dict[str, Any]],
        *,
        model: str | None = None,
        provider: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int = llm_client.DEFAULT_MAX_TOKENS,
    ) -> Iterator[dict[str, Any]]:
        del provider, tools, max_tokens
        # Identify the active scenario from the first user message
        first_user = next((m.get("content", "") for m in messages if m.get("role") == "user"), "")
        if not isinstance(first_user, str):
            first_user = ""
        scenario = scenario_by_prompt.get(first_user.strip())
        if scenario is None:
            yield {"type": "text_delta", "text": "ok"}
            yield {"type": "done", "stop_reason": "end_turn", "usage": {}}
            return
        i = cursor.setdefault(scenario.id, 0)
        if i >= len(scenario.expected_updates):
            yield {"type": "text_delta", "text": "All updates applied."}
            yield {"type": "done", "stop_reason": "end_turn", "usage": {}}
            return
        upd = scenario.expected_updates[i]
        cursor[scenario.id] = i + 1
        instruction = "; ".join(c.text for c in upd.facts_present) or "Apply the documented change."
        yield {
            "type": "tool_call",
            "id": f"call_{scenario.id}_{i}",
            "name": "update_doc_nl",
            "arguments": {"path": upd.path, "instruction": instruction},
        }
        yield {"type": "done", "stop_reason": "tool_use", "usage": {}}

    def _complete(
        messages: list[dict[str, Any]],
        *,
        model: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int = llm_client.DEFAULT_MAX_TOKENS,
    ) -> CompletionResult:
        # Two callers reach ``complete`` during this run:
        #   1. ``wiki_updater.process_instruction`` (via update_doc_nl) —
        #      pretend it made the right change.
        #   2. ``scorers._judge_one_fact`` — judge prompt; return YES so
        #      facts_present / facts_preserved validate scorer wiring.
        del model, tools, max_tokens
        for m in messages:
            if m.get("role") != "system":
                continue
            content = m.get("content", "")
            if isinstance(content, str) and "evaluation judge" in content:
                return CompletionResult(text="YES")
        user_text = "\n".join(m.get("content", "") for m in messages if m.get("role") == "user")
        for s in scenarios:
            for upd in s.expected_updates:
                for d in s.wiki_state:
                    if d.path == upd.path and d.body[:_BODY_FINGERPRINT_LEN] in user_text:
                        extras = "\n".join(f"- {c.text}" for c in upd.facts_present)
                        if extras:
                            return CompletionResult(
                                text=f"{d.body.rstrip()}\n\n## Updates\n\n{extras}\n"
                            )
        return CompletionResult(text=NO_CHANGE_SENTINEL)

    llm_client.stream = _stream  # type: ignore[assignment]
    llm_client.complete = _complete  # type: ignore[assignment]
    try:
        yield
    finally:
        llm_client.stream = original_stream
        llm_client.complete = original_complete


def _run_one_model(
    scenarios: list[Scenario],
    *,
    provider: str,
    model: str,
    judge_model: str | None = None,
) -> Iterator[CaseResult]:
    for scenario in scenarios:
        start = time.monotonic()
        error = ""
        state: WikiState | None = None
        try:
            state = run_scenario(scenario, model=model)
        except Exception as exc:
            error = repr(exc)
            log.warning("scenario %s failed against %s: %s", scenario.id, model, exc)
        if state is None:
            yield CaseResult(
                case_id=scenario.id,
                surface="external_agent",
                provider=provider,
                model=model,
                expected_class=",".join(u.path for u in scenario.expected_updates) or "<none>",
                actual_class="",
                raw_output="",
                scorers=[],
                error=error,
                latency_ms=int((time.monotonic() - start) * 1000),
            )
            continue
        rows = _score_scenario(scenario, state, judge_model=judge_model)
        yield CaseResult(
            case_id=scenario.id,
            surface="external_agent",
            provider=provider,
            model=model,
            expected_class=",".join(sorted(u.path for u in scenario.expected_updates)) or "<none>",
            actual_class=",".join(sorted(state.updated_paths())) or "<none>",
            raw_output=json.dumps(
                {
                    "update_calls": state.update_calls,
                    "final_bodies": {p: state.current_body(p) for p in state.updated_paths()},
                }
            ),
            scorers=rows,
            error=error,
            latency_ms=int((time.monotonic() - start) * 1000),
        )


def _resolve_context(
    provider: str, model: str, dry_run: bool, scenarios: list[Scenario]
) -> ContextManager[None]:
    if dry_run:
        return _stub_external_agent(scenarios)
    return use_model(provider, model)


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Run the external-agent eval.")
    p.add_argument(
        "--scenarios",
        type=Path,
        default=Path("evals/datasets/external_agent/scenarios"),
        help="Directory of scenario YAML files",
    )
    p.add_argument("--models", default="claude-sonnet-4-6")
    p.add_argument(
        "--judge-model",
        default=None,
        help="Model id for LLM-judge scorers (facts_present, facts_preserved). "
        "Defaults to the model under test, but pinning a cheap non-reasoning "
        "model here gives consistent judging across the matrix and lower cost.",
    )
    p.add_argument("--out", type=Path, default=None)
    p.add_argument("--braintrust", default=None)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--scenario-id", default=None)
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--log-level", default="INFO")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv or sys.argv[1:])
    setup_logging(args.log_level)
    scenarios = load_scenarios(args.scenarios)
    if args.scenario_id:
        scenarios = [s for s in scenarios if s.id == args.scenario_id]
        if not scenarios:
            log.error("no scenario with id %s", args.scenario_id)
            return 2
    if args.limit is not None:
        scenarios = scenarios[: args.limit]

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
        log.info("running %d scenarios against %s/%s", len(scenarios), provider, model)
        ctx = _resolve_context(provider, model, args.dry_run, scenarios)
        with ctx:
            for r in _run_one_model(
                scenarios,
                provider=provider,
                model=model,
                judge_model=args.judge_model,
            ):
                all_results.append(r)

    out_path = args.out or Path("runs") / ("external_agent_%d.jsonl" % int(time.time()))
    reporting.write_jsonl(out_path, all_results)
    summary = reporting.summarize(all_results, surface="external_agent")
    reporting.print_summary(summary)
    if args.braintrust:
        reporting.push_to_braintrust(args.braintrust, all_results)
    print(json.dumps({"out": str(out_path), "skipped_models": skipped}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
