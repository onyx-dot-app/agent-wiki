"""Convert ingest_eval_samples export → WikiUpdaterCase JSONL.

Strategy: stratified sample across outcomes (committed / no_change /
irrelevant) and source_types so the resulting case set has balanced
coverage rather than mirroring production's irrelevant-heavy skew.

facts_present / facts_preserved are left empty for v0 — those need
human or LLM labeling. The trigger class + bloat + diff + entity
density + markdown_valid scorers all run regardless.

    # 1. Export from the cluster (scripts/export_eval_samples.sh).
    # 2. Convert to a case JSONL slice:
    cd backend
    uv run python -m scripts.convert_ingest_eval_samples \\
        --src ../scripts/eval_samples_prod.jsonl \\
        --dst evals/datasets/wiki_updater/cases_prod_v0.jsonl
    # 3. Optionally cat into the curated cases.jsonl or pass via --cases.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

from evals.schema import TriggerClass, WikiUpdaterCase

OUTCOME_TO_CLASS: dict[str, TriggerClass] = {
    "committed": TriggerClass.CHANGE,
    "no_change": TriggerClass.NO_CHANGE,
    "irrelevant": TriggerClass.IRRELEVANT,
}

# Per-outcome target — balanced rather than proportional.
TARGET_PER_OUTCOME = {
    "committed": 40,
    "no_change": 25,
    "irrelevant": 35,
}

# Per source-type cap so one connector doesn't dominate.
PER_SOURCE_CAP = 12

# Dedup body-prefix length — matches dry-run stub's _BODY_FINGERPRINT_LEN
# so two cases sharing the same prefix don't trip the stub's collision guard.
_BODY_FINGERPRINT_LEN = 200


def load(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def stratified_sample(rows: list[dict[str, Any]], seed: int = 42) -> list[dict[str, Any]]:
    rng = random.Random(seed)
    by_outcome: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        out = r.get("outcome")
        if out not in OUTCOME_TO_CLASS:
            continue
        # Skip rows with empty source content or wiki body — useless
        if not (r.get("source_content") or "").strip():
            continue
        if r.get("outcome") != "irrelevant" and not (r.get("wiki_body_before") or "").strip():
            continue
        by_outcome[out].append(r)

    # Dedup by (wiki_body_before[:_BODY_FINGERPRINT_LEN]) across the WHOLE
    # picked set so the dry-run stub's fingerprint guard doesn't fire — many
    # prod rows share the same wiki body (same page reconciled vs many sources).
    # Empty bodies dedup the same way: two cases with empty current_body would
    # collide in the stub just as cleanly as two with the same first 200 chars.
    seen_fingerprints: set[str] = set()
    picked: list[dict[str, Any]] = []
    for outcome, target in TARGET_PER_OUTCOME.items():
        pool = by_outcome[outcome]
        rng.shuffle(pool)
        per_src: dict[str, int] = defaultdict(int)
        chosen: list[dict[str, Any]] = []
        for r in pool:
            src = r.get("source_type") or "unknown"
            if per_src[src] >= PER_SOURCE_CAP:
                continue
            fp = (r.get("wiki_body_before") or "")[:_BODY_FINGERPRINT_LEN]
            if fp in seen_fingerprints:
                continue
            seen_fingerprints.add(fp)
            chosen.append(r)
            per_src[src] += 1
            if len(chosen) >= target:
                break
        picked.extend(chosen)
        print(
            "  %s: picked %d/%d (target %d); source_mix=%s"
            % (outcome, len(chosen), len(pool), target, dict(per_src))
        )
    return picked


def to_wiki_updater_case(row: dict[str, Any]) -> dict[str, Any]:
    """Build a schema-valid WikiUpdaterCase dict.

    Round-trips through the pydantic model so schema drift (renamed field,
    new required field, narrower type) raises at generation time instead
    of silently producing JSONL the eval runner rejects later.
    """
    case = WikiUpdaterCase(
        id="prod-recon-%05d" % row["id"],
        surface="reconcile_document",
        wiki_path=row["wiki_path"],
        current_body=row.get("wiki_body_before") or "",
        expected_class=OUTCOME_TO_CLASS[row["outcome"]],
        doc_title=row.get("source_title") or "",
        doc_url=row.get("source_url") or "",
        doc_content=row.get("source_content") or "",
        source="connector:%s" % (row.get("source_type") or "unknown"),
        # facts_* deferred to v1 (labeling pass)
        expected_facts_present=[],
        expected_facts_preserved=[],
        max_bloat_ratio=2.0,
        notes="Mined from production ingest_eval_samples row %s (%s). v0 — no fact labels yet."
        % (row["id"], row.get("created_at") or ""),
        tags=[
            "prod-mined",
            "prod-v0",
            "source:%s" % (row.get("source_type") or "unknown"),
        ],
    )
    return case.model_dump(mode="json")


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Convert ingest_eval_samples → WikiUpdaterCase JSONL")
    p.add_argument(
        "--src",
        type=Path,
        required=True,
        help="Path to the exported ingest_eval_samples JSONL (one row per line)",
    )
    p.add_argument(
        "--dst",
        type=Path,
        required=True,
        help="Output WikiUpdaterCase JSONL",
    )
    p.add_argument("--seed", type=int, default=42)
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv or sys.argv[1:])
    rows = load(args.src)
    print("loaded %d rows from %s" % (len(rows), args.src))
    print("sampling:")
    picked = stratified_sample(rows, seed=args.seed)
    cases = [to_wiki_updater_case(r) for r in picked]
    print("\nwriting %d cases to %s" % (len(cases), args.dst))
    args.dst.parent.mkdir(parents=True, exist_ok=True)
    with args.dst.open("w") as fh:
        for c in cases:
            fh.write(json.dumps(c))
            fh.write("\n")
    print("done: %s (%d bytes)" % (args.dst, args.dst.stat().st_size))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
