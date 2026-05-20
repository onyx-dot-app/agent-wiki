# v0 live baseline

First end-to-end live run of every eval surface. Captured 2026-05-20.

## Setup

- Subject models: `claude-sonnet-4-6`, `claude-opus-4-7`, `gpt-5` (selector additionally `claude-haiku-4-5` and `gpt-5-mini`)
- LLM-judge model: `claude-haiku-4-5` (`--judge-model`) — consistent judge across the matrix, lower cost, no reasoning-token confounders
- Keys: `EVAL_ANTHROPIC_API_KEY`, `EVAL_OPENAI_API_KEY` (env)
- All numbers below are run-local; not pushed to Braintrust this round (CI secrets not yet configured)

## wiki_updater — `process_instruction` (17 cases)

| model             | trigger_class_match | facts_present | facts_preserved | bloat_ratio | markdown_valid | error_rate |
| ----------------- | ------------------- | ------------- | --------------- | ----------- | -------------- | ---------- |
| claude-opus-4-7   | 1.00                | 0.91          | 1.00            | 1.00        | 1.00           | 0.00       |
| claude-sonnet-4-6 | 1.00                | 0.91          | 0.91            | 1.00        | 1.00           | 0.00       |
| gpt-5             | 0.88                | 1.00          | 0.77            | 1.00        | 1.00           | 0.00       |

## wiki_updater — `reconcile_document` (13 cases)

| model             | trigger_class_match | facts_present | facts_preserved | bloat_ratio | markdown_valid | error_rate |
| ----------------- | ------------------- | ------------- | --------------- | ----------- | -------------- | ---------- |
| claude-opus-4-7   | 0.85                | 1.00          | 0.93            | 0.92        | 1.00           | 0.00       |
| claude-sonnet-4-6 | 0.92                | 1.00          | 1.00            | 0.88        | 1.00           | 0.00       |
| gpt-5             | 0.77                | 0.92          | 0.67            | 0.90        | 1.00           | 0.00       |

## ingest_selector (5 cases)

| model            | precision | recall | f1   | error_rate |
| ---------------- | --------- | ------ | ---- | ---------- |
| claude-haiku-4-5 | 0.42      | 1.00   | 0.54 | 0.00       |
| gpt-5-mini       | 0.65      | 1.00   | 0.70 | 0.00       |

Both selectors over-keep — perfect recall, weak precision. Failure mode is "default to keeping everything," which is the intended fail-open behavior; eval correctly highlights it as the tuning frontier.

## external_agent (20 long-doc scenarios — see `datasets/external_agent/scenarios/*.yaml`)

| model             | update_precision | update_recall | update_f1 | no_touch_compliance | facts_present_avg | facts_preserved_avg | bloat_ratio_avg |
| ----------------- | ---------------- | ------------- | --------- | ------------------- | ----------------- | ------------------- | --------------- |
| claude-sonnet-4-6 | 1.00             | 1.00          | 1.00      | 1.00                | 0.96              | 0.94                | 0.99            |
| claude-opus-4-7   | 1.00             | 0.95          | 0.95      | 1.00                | 0.90              | 0.88                | 0.99            |
| gpt-5             | 0.95             | 1.00          | 0.95      | 0.98                | 0.91              | 0.93                | 1.00            |

Observed:

- Sonnet 4.6 leads on the long-doc scenarios — perfect routing + best content scores
- Opus 4.7 is perfectly precise but misses one scoped update (recall 0.95)
- GPT-5 finds all the right docs but touches one it shouldn't (precision 0.95, no_touch 0.98)
- All three models stay within bloat budget on long docs

## Cost (this baseline run, public list price)

| model                    | calls | input tok | output tok | input $ | output $ | total $     |
| ------------------------ | ----- | --------- | ---------- | ------- | -------- | ----------- |
| claude-opus-4-7          | 496   | 523,965   | 75,952     | 7.86    | 5.70     | 13.56       |
| gpt-5                    | 405   | 219,818   | 188,887    | 0.27    | 1.89     | 2.16        |
| claude-sonnet-4-6        | 528   | 374,126   | 66,596     | 1.12    | 1.00     | 2.12        |
| claude-haiku-4-5 (judge) | 680   | 226,747   | 5,095      | 0.23    | 0.03     | 0.25        |
| gpt-5-mini               | 5     | 1,798     | 1,305      | <0.01   | <0.01    | <0.01       |
| **total**                |       |           |            |         |          | **~$18.10** |

Includes one mid-flight kill of a broken run (~$5 wasted on a judge bug). With the now-fixed `--judge-model claude-haiku-4-5` flag and the gpt-5 reasoning-token cap fix, a clean full-matrix run is closer to **~$13**.

## Known issues fixed during this baseline

- Judge `max_tokens=8` rejected by OpenAI Responses API (`integer below minimum value`) — raised to `2048` to leave headroom for reasoning-token spend before the visible YES/NO answer.
- Judge calls failed with `model_not_found` when subject was OpenAI but `--judge-model` named a Claude model — added a nested `use_model(provider_of_judge, judge_model)` context inside `_judge_one_fact` so the provider switches for the judge call.
- `external_agent.run` did not accept `--judge-model` — added the flag and threaded it through `_run_one_model` → `_score_scenario`.

## Next steps before scaling the dataset to 200

1. Verify these numbers reproduce across two more full runs (variance from LLM nondeterminism + judge nondeterminism).
2. Hand-inspect every miss to confirm the scorer's verdict is correct (don't grow on a noisy judge).
3. Mine real Braintrust traces from prod for `agent.wiki_updater` / `agent.wiki_updater.ingest` spans — synthesize new cases from the real ones, biased toward whichever class the matrix is uniformly weakest on (today: `reconcile_document` trigger class for GPT-5).
4. Add adversarial scenarios specifically targeting Opus's missed updates (long docs with subtle action items) and GPT-5's no_touch slip.
