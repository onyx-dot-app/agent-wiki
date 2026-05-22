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
