# ingest_selector dataset

One JSONL row per case. Schema is `backend.evals.schema.IngestSelectorCase`.

The selector's job is to take BM25 candidate hits for an incoming document
and drop the false-positives before the expensive reconciler gets called.
Eval is set-precision / set-recall: did the surviving subset match the
labeled relevant set?

## Fields

| field                 | type               | notes                              |
| --------------------- | ------------------ | ---------------------------------- |
| `id`                  | str                | stable, kebab-case                 |
| `doc_title`           | str                | incoming document title            |
| `doc_content`         | str                | incoming document body             |
| `candidates`          | list[{path, body}] | the BM25 hits we feed the selector |
| `expected_kept_paths` | list[str]          | the subset that should survive     |
| `notes`               | str                | human context                      |
| `tags`                | list[str]          | `all-drop`, `large-batch`, etc.    |

## v0 cases (5)

- `sel-01-one-true-hit` — one billing page, two distractors
- `sel-02-multi-hit` — three relevant, one distractor
- `sel-03-no-hits` — all should drop (avoid the model "preferring something")
- `sel-04-single-relevant-among-many` — picks the right one among similar paths
- `sel-05-large-batch` — six candidates, three relevant; stresses batching

Grow by mining real Braintrust traces from `agent.ingest_selector` once a
baseline exists. Always include `all-drop` cases — the selector failure
mode is "default to keeping everything," and that's what these catch.
