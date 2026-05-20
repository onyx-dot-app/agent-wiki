# Wiki agent evals

Offline eval harness for the agents that read and write the wiki. Lives
outside `tests/` because runs hit real LLMs, take minutes, and are
parameterized by `(provider, model)` — not a fit for the pytest grid.

## Surfaces under eval

| Module                                             | What it decides                                  | Eval target                                                         |
| -------------------------------------------------- | ------------------------------------------------ | ------------------------------------------------------------------- |
| `app.llm.agents.wiki_updater.process_instruction`  | MCP path: NO_CHANGE vs new page body             | trigger class + content quality                                     |
| `app.llm.agents.wiki_updater.reconcile_document`   | Ingest path: NO_CHANGE / IRRELEVANT / new body   | trigger class + content quality                                     |
| `app.llm.agents.ingest_selector.select_candidates` | Ingest pre-filter: which BM25 candidates survive | precision / recall over labeled relevant set                        |
| External agent (e.g. Claude Code via MCP)          | When to call `update_doc_nl(path, instruction)`  | precision / recall over labeled update set + reused content scorers |

Two axes per surface: **WHEN** (was the trigger decision right) and **HOW** (was the output any good).

## Layout

```
backend/evals/
  _llm_override.py            context manager: pick provider+model+keys for a run
  scorers.py                  shared scorers (trigger_class_match, facts_present, ...)
  reporting.py                JSONL writer + summary table + Braintrust push
  schema.py                   pydantic Case + Result types
  datasets/
    wiki_updater/cases.jsonl
    ingest_selector/cases.jsonl
    external_agent/scenarios/
  wiki_updater/run.py         CLI: python -m backend.evals.wiki_updater.run
  ingest_selector/run.py      CLI: python -m backend.evals.ingest_selector.run
  external_agent/run.py       CLI: python -m backend.evals.external_agent.run
```

## How to run

```bash
cd backend
uv run python -m evals.wiki_updater.run \
  --cases evals/datasets/wiki_updater/cases.jsonl \
  --models claude-sonnet-4-6,claude-opus-4-7,gpt-5,gemini-2.5-pro \
  --out runs/wiki_updater_$(date +%Y%m%d).jsonl
```

Flags:

- `--cases PATH` — JSONL dataset (one case per line, schema in `schema.py`)
- `--models LIST` — comma-separated model ids; each is matched to a provider by `_llm_override.resolve_provider`
- `--out PATH` — JSONL result sink; pretty summary always prints to stdout
- `--braintrust EXPERIMENT` — also push as a Braintrust experiment named `<EXPERIMENT>`
- `--dry-run` — use a deterministic stub LLM (no API keys needed); validates harness + scorers end-to-end
- `--limit N` — only run the first N cases (smoke test)

## Configuration

Provider keys are read, in order:

1. Per-eval env vars: `EVAL_ANTHROPIC_API_KEY`, `EVAL_OPENAI_API_KEY`, `EVAL_GEMINI_API_KEY`.
2. Generic env vars: `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `GEMINI_API_KEY`.
3. Whatever's stored in `llm_settings` (DB), if `DATABASE_URL` resolves.

If none of the above yield a configured provider for a requested model, the
runner reports the model as `skipped: no key` and continues with the rest.
With `--dry-run`, no keys are needed.

Braintrust project / API key (for `--braintrust`) come from `BRAINTRUST_API_KEY`
and `BRAINTRUST_PROJECT` env vars, or from `braintrust_settings` in the DB.

## Why these axes

The MCP / ingest agents return one of three things:

- `NO_CHANGE` — page already reflects the input
- `IRRELEVANT` — input doesn't match this page (ingest only)
- a new full body — actually update

The trigger axis catches false positives (gratuitous edits → noise, churn)
and false negatives (missed updates → stale wiki). The content axis catches
the bloat and information-loss failure modes called out in `CLAUDE.md`'s
"Open questions" section.

## What this suite does NOT cover

- End-to-end MCP transport / git commit / reindex. Those have unit tests
  under `backend/tests/`.
- UI rendering of changed pages.
- External-agent harness covers the WHEN of agent-driven updates, but it
  does not measure latency or token cost — Braintrust traces do.
