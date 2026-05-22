# external_agent dataset

One YAML file per scenario in `scenarios/`. Schema is
`backend.evals.external_agent.harness.Scenario`.

Each scenario gives the agent a prompt + a seeded wiki, then measures:

- **WHEN axis** — which doc paths it called `update_doc_nl` on. Scored as
  set-precision and set-recall against `expected_updates` paths.
- **HOW axis** — for each expected update, the final body is scored with
  `facts_present` and `facts_preserved` (same scorers as the wiki_updater
  eval, so numbers are comparable across surfaces).

## Fields

| field                  | type                        | notes                                                |
| ---------------------- | --------------------------- | ---------------------------------------------------- |
| `id`                   | str                         | stable id, kebab-case                                |
| `prompt`               | str                         | the user message handed to the agent                 |
| `wiki_state`           | list[{path, summary, body}] | seed pages the agent can read                        |
| `expected_updates`     | list[ExpectedUpdate]        | paths the agent SHOULD update, with quality claims   |
| `expected_not_updated` | list[str]                   | paths the agent MUST NOT touch                       |
| `notes`                | str                         | human context                                        |
| `tags`                 | list[str]                   | `focus`, `restraint`, `distractor`, `multi-doc`, ... |

## v0 scenarios (5)

- `01-targeted-deprecation` — single-doc focus, two distractors
- `02-multi-doc-cache-migration` — three docs need touching, one unrelated must not
- `03-no-change-needed` — restraint: agent must not update anything
- `04-distractor-keyword` — "cache" appears in unrelated docs; agent must stay scoped
- `05-correction` — typo + factual error on one doc; sibling SLA doc must not change

## Adding a scenario

1. Drop `scenarios/NN-short-name.yaml` with the same field set.
2. Run `uv run python -m evals.external_agent.run --dry-run --models claude-sonnet-4-6 --scenario-id NN-short-name`.
3. For new failure modes: prefer a single new scenario over expanding an
   existing one — keeps per-scenario failure mode readable.
