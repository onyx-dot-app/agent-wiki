# Eval scripts

## Exporting samples from the database

Eval samples are written to `ingest_eval_samples` when `INGEST_EVAL_LOGGING=true` is set in the backend environment. Each row captures the source document, the wiki page before reconciliation, the outcome (`committed` / `no_change` / `irrelevant`), and a unified diff for committed edits.

**Option A — psql** (if available locally):

```bash
psql $DATABASE_URL -c "\copy (
  SELECT
    id,
    source_type,
    source_title,
    source_url,
    source_content,
    wiki_path,
    wiki_body_before,
    diff,
    outcome,
    commit_sha,
    created_at
  FROM ingest_eval_samples
  ORDER BY id
) TO STDOUT WITH (FORMAT csv, HEADER false)" \
| python3 -c "
import sys, csv, json
cols = ['id','source_type','source_title','source_url','source_content',
        'wiki_path','wiki_body_before','diff','outcome','commit_sha','created_at']
for row in csv.reader(sys.stdin):
    print(json.dumps(dict(zip(cols, row))))
" > eval_samples.jsonl
```

**Option B — Python** (psql not required, needs `psycopg` or `psycopg2`):

```bash
python3 -c "
import os, json, psycopg
conn = psycopg.connect(os.environ['DATABASE_URL'])
cur = conn.cursor()
cur.execute('''
  SELECT id, source_type, source_title, source_url, source_content,
         wiki_path, wiki_body_before, diff, outcome, commit_sha, created_at
  FROM ingest_eval_samples
  ORDER BY id
''')
cols = ['id','source_type','source_title','source_url','source_content',
        'wiki_path','wiki_body_before','diff','outcome','commit_sha','created_at']
for row in cur:
    print(json.dumps(dict(zip(cols, [str(v) if v is not None else None for v in row]))))
conn.close()
" > eval_samples.jsonl
```

Filter by outcome and/or ID range by adding a `WHERE` clause to either query:

```sql
WHERE id >= 2894
WHERE outcome = 'committed' AND id > 1000
```

The `id` column is a stable integer primary key — use it to refer to specific samples across sessions.

## Using eval_labeler.html

`eval_labeler.html` is a self-contained browser tool for reviewing and labeling samples. It reads and writes the JSONL file directly via the [File System Access API](https://developer.mozilla.org/en-US/docs/Web/API/File_System_Access_API) (Chrome / Edge only).

**Setup**

1. Open `eval_labeler.html` in Chrome.
2. Click **Load file** and select your JSONL file.
3. Optionally enter an Anthropic API key to enable the LLM judge.

**Navigation**

- Prev / Next buttons or arrow keys to move between samples.
- **Jump to** field accepts a sample ID or 1-based position — press Enter or click Go.
- **Jump to unlabeled** skips to the next sample without a human label.

**Labeling**

Click a label button to assign a human label (`label` field) to the current sample. Labels:

| Label | Meaning |
|---|---|
| `committed_critical` | Key fact or correction that clearly belongs and is missing |
| `committed_moderate` | Concrete, focused addition that fits the page's scope |
| `committed_nit` | Minor or cosmetic touch only |
| `no_change_covered` | Wiki already covers the topic — nothing new in source |
| `no_change_extra` | Source is on-topic but detail exceeds the page's scope |
| `irrelevant` | Source has no connection to this page's topic |

**LLM judge**

With an API key set, click **Judge all `<outcome>`** to run `claude-opus-4-7` over every sample with that system outcome. Results are written to `judge_label` and `judge_reasoning` fields. The judge always re-runs (it overwrites existing `judge_label` values). Progress is shown in the queue status indicator.

The judge prompt mirrors the reconciler's relevance and scope rules so that its labels can be compared directly to the system's `outcome` field.

**Saving**

Changes are written back to the file automatically after each label or judge result. There is no explicit save step.

**Metrics shown**

The progress bar and counters track human-labeled samples. The outcome breakdown at the top shows `committed`, `no_change`, and `irrelevant` counts with how many are labeled.
