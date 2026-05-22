#!/usr/bin/env bash
# Export ingest_eval_samples from production to a local JSONL file.
#
# Usage:
#   ./scripts/export_eval_samples.sh [output_path]
#
# Default output: backend/scripts/eval_samples_prod.jsonl
#
# Requirements: kubectl configured for the agent-wiki cluster.

set -euo pipefail

OUTPUT="${1:-$(dirname "$0")/eval_samples_prod.jsonl}"
NAMESPACE="agent-wiki"
POD=$(kubectl get pod -n "$NAMESPACE" -l app.kubernetes.io/component=backend \
      -o jsonpath='{.items[0].metadata.name}')

echo "Exporting from pod $POD → $OUTPUT"

kubectl exec -n "$NAMESPACE" "$POD" -- python -c "
import os, json
from sqlalchemy import create_engine, text

url = os.environ['DATABASE_URL'].replace('postgresql://', 'postgresql+psycopg://')
engine = create_engine(url)
with engine.connect() as conn:
    rows = conn.execute(text('''
        SELECT id, created_at, source_type, source_title, source_url,
               source_content, wiki_path, wiki_body_before, diff, outcome, commit_sha
        FROM ingest_eval_samples
        ORDER BY id
    ''')).fetchall()
    for r in rows:
        print(json.dumps(dict(r._mapping)))
" > "$OUTPUT"

COUNT=$(wc -l < "$OUTPUT" | tr -d ' ')
echo "Exported $COUNT rows to $OUTPUT"
