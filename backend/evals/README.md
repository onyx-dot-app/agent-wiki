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
  _llm_override.py            ContextVar-scoped provider/model override
  _metadata.py                eval_run_id / git_sha / utc_iso_now
  scorers.py                  trigger_class_match, facts_present/preserved,
                              bloat_ratio, entity_density_delta, etc.
  reporting.py                JSONL writer + bootstrap CI summary + Braintrust
  schema.py                   pydantic Case + Result types
  ci_assert_baseline.py       structural validator (used in CI)
  datasets/
    wiki_updater/cases.jsonl
    ingest_selector/cases.jsonl
    external_agent/scenarios/
  wiki_updater/run.py         python -m evals.wiki_updater.run
  ingest_selector/run.py      python -m evals.ingest_selector.run
  external_agent/run.py       python -m evals.external_agent.run
```

## How to run

```bash
cd backend
uv run python -m evals.wiki_updater.run \
  --cases evals/datasets/wiki_updater/cases.jsonl \
  --models claude-sonnet-4-6,gpt-5,gemini-2.5-pro \
  --runs 3 \
  --out runs/wiki_updater_$(date +%Y%m%d).jsonl
```

Flags:

- `--cases PATH` — JSONL dataset (one case per line)
- `--models LIST` — comma-separated model ids; provider inferred from prefix
- `--runs N` — trials per (case, model) for variance / bootstrap CI (default 3)
- `--case-id ID` — run only one case (debugging)
- `--limit N` — only run the first N cases (smoke)
- `--judge-models LIST` — judge panel for `facts_present` / `facts_preserved` (default: three-family panel — `claude-haiku-4-5`, `gpt-5-mini`, `gemini-2.5-flash`)
- `--out PATH` — JSONL result sink; summary always prints
- `--braintrust EXPERIMENT` — also push as a Braintrust experiment
- `--dry-run` — deterministic stub LLM (no keys needed); validates wiring

## Configuration

Provider keys are read in order:

1. Per-eval: `EVAL_ANTHROPIC_API_KEY`, `EVAL_OPENAI_API_KEY`, `EVAL_GEMINI_API_KEY`
2. Generic: `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GEMINI_API_KEY`
3. `llm_settings` table (when `DATABASE_URL` resolves)

Models without a configured provider are reported as `skipped` and the rest run.

Braintrust (`--braintrust`) reads `BRAINTRUST_API_KEY` / `BRAINTRUST_PROJECT` or
`braintrust_settings` in the DB.
