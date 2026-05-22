# Wiki agent evals

Offline eval harness for the agents that read and write the wiki. Lives
outside `tests/` because runs hit real LLMs and are parameterized by
`(provider, model)`.

## Surfaces

| Module                                                   | Decides                                          | Eval target                                         |
| -------------------------------------------------------- | ------------------------------------------------ | --------------------------------------------------- |
| `app.llm.agents.nl_updater.process_instruction`          | MCP path: NO_CHANGE vs new body                  | trigger class + content quality                     |
| `app.llm.agents.ingest_batch_reconciler.batch_reconcile` | Ingest: NO_CHANGE / IRRELEVANT / new body        | trigger class + content quality                     |
| `app.llm.agents.ingest_selector.select_candidates`       | Ingest pre-filter: which BM25 candidates survive | precision / recall / F1                             |
| External agent (Claude Code via MCP)                     | When to call `update_doc_nl(path, instruction)`  | per-doc precision / recall + reused content scorers |

Two axes per surface: **WHEN** (trigger decision) and **HOW** (output quality).

## Layout

```
backend/evals/
  _cli.py                     shared runner skeleton + ThreadPoolExecutor fan-out
  _llm_override.py            ContextVar-scoped (provider, model) override
  _metadata.py                eval_run_id / git_sha / utc_iso_now
  _dry_run.py                 wiki_updater dry-run stub (patches client.complete)
  scorers.py                  trigger_class_match, facts_present/preserved,
                              bloat_ratio, entity_density_delta, etc.
                              Exports JUDGE_SYSTEM_MARKER (shared with stubs).
  reporting.py                JSONL writer + bootstrap CI summary +
                              Braintrust push + GitHub-step-summary writer
  schema.py                   pydantic Case + Result types
  ci_assert_baseline.py       structural validator (used in CI)
  tests/                      unit tests for scorers + reporting (no DB needed)
  datasets/
    wiki_updater/cases.jsonl
    ingest_selector/cases.jsonl
    external_agent/scenarios/
  wiki_updater/run.py         python -m evals.wiki_updater.run
  ingest_selector/run.py      python -m evals.ingest_selector.run
  external_agent/
    run.py                    python -m evals.external_agent.run
    _stub.py                  external-agent dry-run stub
    harness.py                in-memory wiki + tool dispatch
```

## How to run

```bash
cd backend
uv run python -m evals.wiki_updater.run \
  --cases evals/datasets/wiki_updater/cases.jsonl \
  --models claude-sonnet-4-6,gpt-5 \
  --runs 3 \
  --concurrency 8 \
  --out runs/wiki_updater_$(date +%Y%m%d).jsonl
```

Flags (all surfaces):

- `--cases PATH` (or `--scenarios DIR` for external_agent) — dataset
- `--models LIST` — comma-separated model ids; provider inferred from prefix
- `--runs N` — trials per (case, model) for variance + bootstrap CI (default 3)
- `--concurrency N` — max in-flight LLM calls (default 8). Lower if you hit
  provider rate limits.
- `--case-id ID` — run only one case (debugging). External_agent also accepts
  `--scenario-id` as an alias.
- `--limit N` — only run the first N cases
- `--out PATH` — JSONL result sink; summary always prints
- `--braintrust BASE` — push to a Braintrust experiment of this base name.
  `wiki_updater` produces two experiments — `<base>-process-instruction` and
  `<base>-reconcile-document` — so scorer columns stay comparable per surface.
- `--dry-run` — deterministic stub LLM (no API keys needed); validates wiring
- `wiki_updater` / `external_agent` also accept `--judge-models LIST` — judge
  panel for `facts_present` / `facts_preserved`. Default panel:
  `claude-haiku-4-5, gpt-5-mini` (two judges, majority + tie→False).

Runners exit with `{"out": ..., "skipped_models": [...], "braintrust_url(s)": ...}`
to stdout so the CI workflow can capture the artifact path and experiment URL.

## GitHub Actions integration

When `$GITHUB_STEP_SUMMARY` is set (every Actions run), runners append a
markdown section per surface with the same scorer table that prints to the
terminal, plus a clickable Braintrust experiment link. Open the job summary
page on a CI run to see eval results inline without leaving GitHub.

Workflows:

- `.github/workflows/evals-smoke.yml` — PR-time dry-run smoke + unit tests.
  Triggers only when files under `backend/evals/` or the agents it exercises
  change.
- `.github/workflows/evals-nightly.yml` — schedule + `workflow_dispatch` for
  live model-matrix runs. Reads `EVAL_ANTHROPIC_API_KEY` /
  `EVAL_OPENAI_API_KEY` / `BRAINTRUST_API_KEY` from secrets. Defaults to
  `claude-sonnet-4-6,gpt-5` for wiki_updater + external_agent and
  `claude-haiku-4-5,gpt-5-mini` for ingest_selector.

## Concurrency model

`evals._cli.run_concurrent` fans every `(case, provider/model, run_index)`
trial out across a `ThreadPoolExecutor`. Each worker gets its own
`contextvars.copy_context()` copy so the per-(provider, model) override
installed by `use_model` is thread-local. Dry-run stubs patch the
module-global `app.llm.client.complete`/`.stream` and are entered ONCE
around the whole pool, so per-worker entry/exit can't race.

Output JSONL is sorted on `(case_id, provider, model, run_index)` so the
file is byte-stable across runs regardless of executor scheduling.

## Configuration

Provider keys are read in order:

1. Per-eval: `EVAL_ANTHROPIC_API_KEY`, `EVAL_OPENAI_API_KEY`, `EVAL_GEMINI_API_KEY`
2. Generic: `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GEMINI_API_KEY`
3. `llm_settings` table (when `DATABASE_URL` resolves)

Models without a configured provider are reported as `skipped` and the rest run.

Braintrust (`--braintrust`) reads `BRAINTRUST_API_KEY` + `BRAINTRUST_PROJECT`
from env (or `braintrust_settings` in the DB). `BRAINTRUST_ORG` controls the
URL emitted in stdout / GHA summary (defaults to empty — set the env var or
the GitHub repo `vars.BRAINTRUST_ORG`).

## Scorer reference

Every scorer returns a value in `[0.0, 1.0]` where **higher = better** — except `error_rate`, where **0 = clean** (lower is better). The summary table prints `mean [ci_low, ci_high]` per scorer per model. Bootstrap CI = 1000 case-level resamples.

| scorer                                             | what it measures                                                                                                 | direction             | default threshold                        | what failure looks like                                                                                |
| -------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------- | --------------------- | ---------------------------------------- | ------------------------------------------------------------------------------------------------------ |
| `trigger_class_match`                              | exact-match on expected vs actual class (per-trial)                                                              | 1 = correct           | n/a (binary)                             | model picks NO_CHANGE when CHANGE was right, or vice versa                                             |
| `trigger.precision[<cls>]`                         | of trials model called `<cls>`, fraction actually were                                                           | per-class             | n/a                                      | precision << recall → over-triggers that class                                                         |
| `trigger.recall[<cls>]`                            | of trials that should have been `<cls>`, fraction caught                                                         | per-class             | n/a                                      | recall << precision → misses that class                                                                |
| `trigger.macro_f1`                                 | mean of per-class F1 (handles class imbalance)                                                                   | 1 = perfect           | n/a                                      | < 0.85 = real routing problem                                                                          |
| `bloat_ratio`                                      | 1.0 if `len(new)/len(current) ≤ max_ratio` (default 2.0), scaled penalty past that                               | 1 = within budget     | per-case `max_bloat_ratio` (default 2.0) | < 1.0 = body more than doubled                                                                         |
| `diff_addition_ratio`                              | tokens added vs current via `difflib.SequenceMatcher`; 1.0 if added ≤ 50%, scaled past that                      | 1 = surgical edit     | `passed` when ratio ≤ 0.75               | < 0.7 = verbose rewrite where most tokens are replaced (catches "looks edited but actually rewritten") |
| `entity_density_delta`                             | per-100-token delta in entity count (paths, code idents, Title Case, versions, units); 1 if delta ≤ ±2, 0 at ±10 | 1 = stable density    | ±4 density delta                         | < 0.6 = big density swing → info loss (drop) or wall of jargon (jump)                                  |
| `markdown_valid`                                   | real CommonMark parse via `markdown-it-py`; catches heading-level jumps + structural drift                       | 1 = valid             | n/a                                      | < 1.0 = heading skipped a level or table malformed                                                     |
| `selector_set_metrics` (`precision`/`recall`/`f1`) | set-overlap over labeled kept paths                                                                              | 1 = perfect           | 0.8 pass                                 | precision << recall = over-keeps (fires too many downstream calls); inverse = misses relevant docs     |
| `facts_present` (judge panel)                      | fraction of `expected_facts_present` the panel agrees are in the new body                                        | 1 = all facts landed  | 0.8 pass                                 | < 0.8 = agent failed to integrate new info                                                             |
| `facts_preserved` (judge panel)                    | fraction of `expected_facts_preserved` the panel agrees survived the edit                                        | 1 = no loss           | 0.9 pass                                 | < 0.9 = agent dropped facts that should have persisted (info-loss failure)                             |
| `no_touch_compliance` (external_agent)             | fraction of `expected_not_updated` paths whose body is unchanged                                                 | 1 = perfect restraint | 1.0 pass                                 | < 1.0 = agent touched a doc that didn't need updating                                                  |
| `update_precision`/`recall`/`f1` (external_agent)  | set-overlap on `expected_updates` paths the agent actually changed                                               | 1 = perfect routing   | 0.8 pass                                 | precision low = over-eager; recall low = misses an obvious update                                      |
| `error_rate`                                       | fraction of cases where the trial raised an exception                                                            | 0 = clean             | n/a                                      | > 0 = harness or provider error — check per-row `error` field                                          |

LLM-judge details:

- Each `expected_fact*` is scored by **every** model in the judge panel (default: `claude-haiku-4-5`, `gpt-5-mini`).
- Verdicts: `YES` / `NO` / `UNKNOWN`. UNKNOWN abstains.
- Majority wins; ties → False (conservative — surfaces as a regression rather than silent pass).
- Per-judge rationale stored in the row's `detail` field for post-hoc inspection.

## Reading a Braintrust experiment

Each row in a BT experiment = one trial = one `(case_id, provider, model, run_index)` tuple. The experiment-level scorer column shows the mean across all rows in the experiment, with the per-row score visible on click-through.

What to scan in order:

1. **`error_rate` first.** Non-zero = the harness or provider crashed. Look at the per-row `error` field. Until that's 0, ignore the other scorers — they're sampled from a degraded subset.
2. **`trigger_class_match` + `trigger.macro_f1`** for wiki_updater. Low here = model is making the wrong decision before quality even matters.
3. **`facts_preserved` < 0.9** = info-loss failure (agent dropped facts that should have stayed). Worse than `facts_present` < 0.8 (failed to add new info) because it silently degrades the wiki over time.
4. **Wide CI** (e.g. `0.50 [0.20, 0.80]`) = either small dataset (need more cases) OR high run-to-run variance (need more `--runs N`). Re-run with `--runs 5` to disambiguate.
5. **`diff_addition_ratio` < 0.7**: agent is rewriting most tokens even though net body length looks fine. Pull 2-3 rows, click into the BT trace, eyeball the `raw_output` field — usually means the prompt is encouraging "summarize the whole doc" instead of "splice in the new fact".
6. **Compare models on the same scorer column** — Braintrust shows ∆ vs the baseline model when you set one as the comparand. Stable signal needs at least ~15 cases × 3 runs to overcome CI overlap.

Comparing across runs:

- BT diffs experiments by their **shared `case_id`** values. The framework guarantees stable `case_id` across runs, so opening two experiments side-by-side gives a true regression view.
- `eval_run_id` is NOT pushed to Braintrust metadata — only `provider`, `model`, `run_index`, `error`, `latency_ms`, and token counts make it onto a BT row (see `reporting.push_to_braintrust`). To trace a BT row back to a CI run, match on `case_id` + `model` + `run_index` against the JSONL artifact uploaded by the workflow; the JSONL carries the full `eval_run_id` / `harness_git_sha` / `dataset_git_sha`.

## Contributing

### Adding a new case to an existing surface

1. **wiki_updater**: append a JSON line to `evals/datasets/wiki_updater/cases.jsonl`. Schema is `WikiUpdaterCase` in `evals/schema.py` — minimum: `id`, `surface` (`process_instruction` | `reconcile_document`), `wiki_path`, `current_body`, `expected_class` (NO_CHANGE | CHANGE | IRRELEVANT). For CHANGE cases also fill `expected_facts_present` + `expected_facts_preserved` (each `{id, text}`).
2. **ingest_selector**: append to `evals/datasets/ingest_selector/cases.jsonl`. `IngestSelectorCase` — `id`, `doc_title`, `doc_content`, `candidates` (each `{path, body}`), `expected_kept_paths`.
3. **external_agent**: drop a YAML file in `evals/datasets/external_agent/scenarios/`. `Scenario` shape: `id`, `prompt`, `wiki_state` (list of `{path, body, summary}`), `expected_updates` (each `{path, facts_present, facts_preserved, max_bloat_ratio}`), `expected_not_updated` (list of paths).
4. Run the runner with `--dry-run --case-id <id>` to verify the stub handles the case before spending API tokens.

**What makes a good adversarial case**:

- `bloat-bait` — long verbose payload, zero new info. Tests whether the agent appends churn.
- `loss-bait` — small targeted change against a fact-dense page. Tests whether the agent rewrites from scratch.
- `irrelevant-bait` — payload that shares keywords with the page but isn't actually relevant. Tests whether the ingest path correctly rejects.
- `distractor` (external_agent) — sibling docs that share keywords with the target. Tests routing precision.

### Adding a new surface

1. New module under `evals/<surface_name>/`. Mandatory files: `run.py`, `_stub.py` (if dry-run is wanted), `__init__.py`.
2. Implement `_run_one(case, provider, model, run_index) -> (Surface, expected_class, actual_class, raw_output, [ScorerOutcome])`.
3. In `main()`, call `_cli.add_common_args(parser, default_models=...)` then `_cli.resolve_runnable(...)` + `_cli.build_metadata(...)` + `_cli.run_concurrent(...)`. Pass `dry_run_ctx=<your_stub>(cases)` if dry-run is supported.
4. Add the new `Surface` literal to `evals/schema.py:Surface`.
5. Wire CI: add a new step in `.github/workflows/evals-smoke.yml` with `--dry-run --runs 1`.
6. Document the surface in the "Surfaces" table above + add a section to `evals/datasets/<surface_name>/README.md`.

### Debugging a failing trial

1. Open the BT experiment, find the row with low score or non-empty `error` field. Note `case_id`, `model`, and the `raw_output` / `error` payload.
2. Reproduce locally with `--case-id <id> --models <model> --runs 1` (no concurrency for clarity: add `--concurrency 1`).
3. If it's an LLM behavior issue, set `--log-level DEBUG` to see the full prompt + raw response.
4. If it's a scorer issue, write a unit test in `evals/tests/test_scorers.py` reproducing the input → expected score mapping. Fix the scorer, the test guards the fix.
5. For external_agent: the harness `WikiState.update_calls` list captures every `update_doc_nl` invocation. Inspect that to see whether the agent loop did the wrong thing or `process_instruction` did.

### Cost notes

A typical full nightly run (4 surfaces, 3 runs each, default judge panel) on `claude-sonnet-4-6` + `gpt-5` for subjects and `claude-haiku-4-5` + `gpt-5-mini` for judges costs roughly **$15-25** end-to-end (~10-15 min wall time at `--concurrency 8`). The selector surface is cheap (~$0.30); external_agent is the most expensive due to multi-step agent loops. Drop `--concurrency` if you hit provider rate limits; raise `--runs` to tighten bootstrap CIs at linear cost.
