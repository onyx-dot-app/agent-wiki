# wiki_updater dataset

One JSONL row per case. Schema is `backend.evals.schema.WikiUpdaterCase`.

## Fields

| field                                 | type                                              | notes                                                         |
| ------------------------------------- | ------------------------------------------------- | ------------------------------------------------------------- |
| `id`                                  | str                                               | stable, kebab-case, surface-prefixed                          |
| `surface`                             | `"process_instruction"` \| `"reconcile_document"` | which agent call this row drives                              |
| `wiki_path`                           | str                                               | must be unique across the dataset (stub LLM uses it as a key) |
| `current_body`                        | str                                               | the page body before the call                                 |
| `expected_class`                      | `"NO_CHANGE"` \| `"IRRELEVANT"` \| `"CHANGE"`     | trigger ground truth                                          |
| `payload`                             | dict                                              | only for `process_instruction`                                |
| `source`                              | str                                               | only for `process_instruction`                                |
| `doc_title`, `doc_url`, `doc_content` | str                                               | only for `reconcile_document`                                 |
| `expected_facts_present`              | list[FactClaim]                                   | only for `CHANGE` — facts the new body must contain           |
| `expected_facts_preserved`            | list[FactClaim]                                   | facts the new body must still contain (loss-check)            |
| `max_bloat_ratio`                     | float                                             | default 2.0; tighter for bloat-bait                           |
| `notes`                               | str                                               | human context, not used by scorers                            |
| `tags`                                | list[str]                                         | `bloat-bait`, `loss-bait`, `irrelevant-bait`, etc.            |

## v0 distribution (30 cases)

| surface               | class      | count | tags              |
| --------------------- | ---------- | ----- | ----------------- |
| `process_instruction` | NO_CHANGE  | 5     | —                 |
| `process_instruction` | CHANGE     | 8     | —                 |
| `process_instruction` | CHANGE     | 2     | `bloat-bait`      |
| `process_instruction` | CHANGE     | 2     | `loss-bait`       |
| `reconcile_document`  | NO_CHANGE  | 3     | —                 |
| `reconcile_document`  | CHANGE     | 4     | —                 |
| `reconcile_document`  | IRRELEVANT | 4     | `irrelevant-bait` |
| `reconcile_document`  | CHANGE     | 1     | `bloat-bait`      |
| `reconcile_document`  | CHANGE     | 1     | `loss-bait`       |

## Adding a case

1. Write the row as a single JSON line at the bottom of `cases.jsonl`.
2. Run `uv run python -m evals.wiki_updater.run --dry-run --limit 1 --cases evals/datasets/wiki_updater/cases --case-id <new-id>` to validate parse.
3. For `CHANGE` rows: include at least one `expected_facts_present` claim and at least one `expected_facts_preserved` claim. Otherwise the quality scorers can't catch regressions.

## Growth plan

v0 = 30 hand-curated cases (this commit). After three consecutive runs across
the full model matrix show stable rankings (no model jumps more than 0.05 on
any aggregate scorer between runs), grow to 200 by:

1. Mining real Braintrust traces from prod for `agent.wiki_updater` / `agent.wiki_updater.ingest` spans.
2. Synthesizing pairs from real wiki commit history (commit message as payload, before-state as `current_body`, observed change as ground truth).
3. Filling adversarial gaps revealed by the v0 baseline (whichever class the matrix is uniformly bad at).
