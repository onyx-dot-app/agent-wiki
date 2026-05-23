#!/usr/bin/env bash
# Export the `ingest_eval_samples` table from a running agent-wiki cluster
# to a local JSONL (one row per line). The table is populated when the
# `INGEST_EVAL_LOGGING` env var is set on backend + workers (see
# `app/ingest/eval_sample.py`).
#
# The JSONL is intended as input to `convert_ingest_eval_samples.py`,
# which slices it into a `WikiUpdaterCase` dataset for the eval harness.
#
# Usage:
#     ./backend/scripts/export_ingest_eval_samples.sh [output_path]
#
# Defaults: ${1:-/tmp/ingest_eval_samples.jsonl}. Requires kubectl
# pointed at the cluster (e.g. `kubectl config use-context
# agent-wiki-dev-wiki`) and a running backend pod in the `agent-wiki`
# namespace.
set -euo pipefail

OUT="${1:-/tmp/ingest_eval_samples.jsonl}"
NS="agent-wiki"
POD=$(kubectl get pod -n "$NS" -l app.kubernetes.io/component=backend \
      -o jsonpath='{.items[0].metadata.name}')

echo "Exporting from $POD ($(kubectl config current-context)) -> $OUT"

kubectl exec -n "$NS" "$POD" -- python -c "
import os, json
from sqlalchemy import create_engine, text

url = os.environ['DATABASE_URL'].replace('postgresql://', 'postgresql+psycopg://')
engine = create_engine(url)
with engine.connect() as conn:
    rows = conn.execute(text('''
        SELECT id, created_at, source_document_id, source_type, source_title,
               source_url, source_content, wiki_path, wiki_body_before, diff,
               outcome, commit_sha
        FROM ingest_eval_samples
        ORDER BY id
    ''')).fetchall()
    for r in rows:
        print(json.dumps(dict(r._mapping), default=str))
" > "$OUT"

COUNT=$(wc -l < "$OUT" | tr -d ' ')
echo "Exported $COUNT rows."
