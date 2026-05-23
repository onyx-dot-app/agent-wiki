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
import re
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


# --------------------------------------------------------------------------- #
# PII redaction                                                                #
# --------------------------------------------------------------------------- #
#
# Production `ingest_eval_samples.source_content` includes raw HubSpot
# contacts (real customer emails, deal sizes), Slack messages, GitHub PRs,
# etc. None of that may enter a version-controlled JSONL. `redact_pii`
# regex-scrubs the obvious leaks: emails, dollar amounts, URLs, phone
# numbers, long digit strings (stage / pipeline ids), and any token that
# looks like a `*.com` / `*.io` / `*.net` domain.
#
# This is a defense-in-depth pass — high-precision regexes only. Anything
# subtle (free-form prose mentioning a customer name without an email
# anchor) should be caught by a labeling pass before committing, not by
# regex. The `prod-mined` tag exists so reviewers can spot-check.

_EMAIL_RE = re.compile(r"\b[\w.+-]+@[\w-]+(?:\.[\w-]+)+\b")
_URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)
_DOMAIN_RE = re.compile(r"\b[a-z0-9-]+\.(?:com|io|net|org|co|app|ai|dev)\b", re.IGNORECASE)
_AMOUNT_RE = re.compile(r"\$\s?\d[\d,]*(?:\.\d+)?(?:\s?[kKmMbB])?\b")
_PHONE_RE = re.compile(r"\+?\d[\d\s().-]{7,}\d")
_LONG_DIGITS_RE = re.compile(r"\b\d{6,}\b")


def _identity(text: str) -> str:
    return text


def redact_pii(text: str) -> str:
    """Replace high-precision PII patterns with stable placeholders.

    Order matters: URLs first (which contain `.com`s), then emails (which
    also contain `.com`s), then bare domains, then numerics. Each
    placeholder is unambiguous so a reviewer can grep for `<EMAIL>` /
    `<AMOUNT>` to confirm nothing real slipped through.
    """
    text = _URL_RE.sub("<URL>", text)
    text = _EMAIL_RE.sub("<EMAIL>", text)
    text = _DOMAIN_RE.sub("<DOMAIN>", text)
    text = _AMOUNT_RE.sub("<AMOUNT>", text)
    text = _PHONE_RE.sub("<PHONE>", text)
    text = _LONG_DIGITS_RE.sub("<ID>", text)
    return text


def stratified_sample(
    rows: list[dict[str, Any]], seed: int = 42, *, redact: bool = True
) -> list[dict[str, Any]]:
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

    # Dedup by the POST-redaction fingerprint so the dry-run stub's match
    # works on the same string the case carries. Pre-redaction bodies that
    # differ only by an email or dollar amount collapse to the same redacted
    # prefix; the stub can't tell them apart, so we must drop the dup here.
    scrub = redact_pii if redact else _identity
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
            raw_body = r.get("wiki_body_before") or ""
            fp = scrub(raw_body)[:_BODY_FINGERPRINT_LEN]
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


def to_wiki_updater_case(row: dict[str, Any], *, redact: bool = True) -> dict[str, Any]:
    """Build a schema-valid WikiUpdaterCase dict.

    Round-trips through the pydantic model so schema drift (renamed field,
    new required field, narrower type) raises at generation time instead
    of silently producing JSONL the eval runner rejects later.

    ``redact`` (default True) scrubs PII from doc_title / doc_content /
    current_body before the case is built. Always leave it on for any
    output destined for version control.
    """

    scrub = redact_pii if redact else _identity
    case = WikiUpdaterCase(
        id="prod-recon-%05d" % row["id"],
        surface="reconcile_document",
        wiki_path=row["wiki_path"],
        current_body=scrub(row.get("wiki_body_before") or ""),
        expected_class=OUTCOME_TO_CLASS[row["outcome"]],
        doc_title=scrub(row.get("source_title") or ""),
        doc_url="" if redact else (row.get("source_url") or ""),
        doc_content=scrub(row.get("source_content") or ""),
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
            "redacted" if redact else "raw",
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
    p.add_argument(
        "--no-redact",
        dest="redact",
        action="store_false",
        help="Skip PII redaction (raw passthrough). NEVER use for output destined for git.",
    )
    p.set_defaults(redact=True)
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv or sys.argv[1:])
    rows = load(args.src)
    print("loaded %d rows from %s" % (len(rows), args.src))
    print("sampling:")
    picked = stratified_sample(rows, seed=args.seed, redact=args.redact)
    cases = [to_wiki_updater_case(r, redact=args.redact) for r in picked]
    print("redact=%s" % args.redact)
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
