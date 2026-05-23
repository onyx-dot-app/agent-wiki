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
CONTEXT="$(kubectl config current-context)"

echo "kubectl context: $CONTEXT"
read -r -p "Proceed with export from namespace '$NS'? [y/N] " CONFIRM
if [[ ! "$CONFIRM" =~ ^[Yy]$ ]]; then
  echo "Aborted."
  exit 1
fi

POD="$(kubectl get pod -n "$NS" -l app.kubernetes.io/component=backend \
      -o jsonpath='{.items[0].metadata.name}')"

if [[ -z "$POD" ]]; then
  echo "No backend pod found in namespace '$NS'." >&2
  exit 2
fi

mkdir -p "$(dirname "$OUT")"
echo "Exporting from $POD ($CONTEXT) -> $OUT"

kubectl exec -n "$NS" "$POD" -- python - <<'PY' > "$OUT"
import json
import os

from sqlalchemy import MetaData, create_engine, select
from sqlalchemy.orm import Session


def _sa_url(database_url: str) -> str:
    if database_url.startswith("postgresql://"):
        return "postgresql+psycopg://" + database_url[len("postgresql://") :]
    return database_url


database_url = os.environ.get("DATABASE_URL")
if not database_url:
    raise RuntimeError("DATABASE_URL missing in target pod environment")

url = _sa_url(database_url)
engine = create_engine(url)
metadata = MetaData()
table = metadata.tables.get("ingest_eval_samples")
if table is None:
    metadata.reflect(bind=engine, only=("ingest_eval_samples",))
    table = metadata.tables["ingest_eval_samples"]

stmt = (
    select(table.c.id, table.c.created_at, table.c.source_document_id, table.c.source_type)
    .add_columns(
        table.c.source_title,
        table.c.source_url,
        table.c.source_content,
        table.c.wiki_path,
        table.c.wiki_body_before,
        table.c.diff,
        table.c.outcome,
        table.c.commit_sha,
    )
    .order_by(table.c.id)
)

with Session(engine) as session:
    for row in session.execute(stmt).mappings():
        print(json.dumps(dict(row), default=str))
PY

COUNT=$(wc -l < "$OUT" | tr -d ' ')
echo "Exported $COUNT rows."
