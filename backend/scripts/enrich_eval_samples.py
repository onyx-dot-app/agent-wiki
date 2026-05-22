#!/usr/bin/env python3
"""
Enrich an ingest_eval_samples JSONL with LLM-generated summaries.

For each row adds:
  source_summary  — 2-3 sentences on what the source document is about
  wiki_summary    — 2-3 sentences on what the wiki page currently covers
  diff_summary    — 1-2 sentences on what the committed edit changes
                    (committed rows only)

Rows that already have all applicable summary fields are skipped, so the
script is safe to re-run (resume after interruption).

Usage:
  cd backend
  ANTHROPIC_API_KEY=sk-... uv run python scripts/enrich_eval_samples.py \\
      scripts/eval_samples_prod.jsonl

Output is written to <stem>_enriched.jsonl next to the input file.
Pass --overwrite to replace the input file in-place instead.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import anthropic

MODEL = "claude-haiku-4-5-20251001"
SOURCE_MAX = 6000   # chars sent to LLM — haiku context is generous
WIKI_MAX   = 6000
DIFF_MAX   = 3000


def _prompt(source: str, wiki: str, diff: str | None) -> str:
    parts = [
        f"<source_document>\n{source}\n</source_document>",
        f"<wiki_page_before>\n{wiki}\n</wiki_page_before>",
    ]
    if diff:
        parts.append(f"<diff>\n{diff}\n</diff>")

    fields = '"source_summary": "...", "wiki_summary": "..."'
    if diff:
        fields += ', "diff_summary": "..."'

    return "\n\n".join(parts) + f"""

Respond with JSON only — no prose, no markdown fences:
{{{fields}}}

source_summary: 2-3 sentences on what the source document is about.
wiki_summary: 2-3 sentences on what the wiki page currently covers.
{"diff_summary: 1-2 sentences on what the committed edit adds or changes." if diff else ""}
""".strip()


def _already_enriched(row: dict) -> bool:
    has_source = bool(row.get("source_summary"))
    has_wiki   = bool(row.get("wiki_summary"))
    has_diff   = bool(row.get("diff_summary")) if row.get("diff") else True
    return has_source and has_wiki and has_diff


def enrich_row(client: anthropic.Anthropic, row: dict) -> dict:
    source = (row.get("source_content") or "")[:SOURCE_MAX]
    wiki   = (row.get("wiki_body_before") or "")[:WIKI_MAX]
    diff   = (row.get("diff") or "")[:DIFF_MAX] or None

    prompt = _prompt(source, wiki, diff)

    resp = client.messages.create(
        model=MODEL,
        max_tokens=512,
        system="You are a concise technical summarizer. Reply only with the requested JSON object.",
        messages=[{"role": "user", "content": prompt}],
    )

    text = resp.content[0].text.strip()
    # Strip markdown fences if the model added them despite instructions
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()

    summaries: dict = json.loads(text)
    row = {**row}
    row["source_summary"] = summaries.get("source_summary") or ""
    row["wiki_summary"]   = summaries.get("wiki_summary") or ""
    if diff:
        row["diff_summary"] = summaries.get("diff_summary") or ""
    return row


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("input", help="Path to the .jsonl file to enrich")
    parser.add_argument("--overwrite", action="store_true", help="Replace the input file instead of writing *_enriched.jsonl")
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        sys.exit(f"File not found: {input_path}")

    output_path = input_path if args.overwrite else input_path.with_stem(input_path.stem + "_enriched")

    rows = [json.loads(l) for l in input_path.read_text().splitlines() if l.strip()]
    print(f"Loaded {len(rows)} rows from {input_path}")

    to_enrich = [r for r in rows if not _already_enriched(r)]
    print(f"{len(rows) - len(to_enrich)} already enriched, {len(to_enrich)} to process")

    if not to_enrich:
        print("Nothing to do.")
        return

    client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from env

    enriched_map: dict[int, dict] = {r["id"]: r for r in rows}

    for i, row in enumerate(to_enrich, 1):
        print(f"  [{i}/{len(to_enrich)}] id={row['id']} outcome={row.get('outcome','?')} ...", end=" ", flush=True)
        try:
            enriched = enrich_row(client, row)
            enriched_map[enriched["id"]] = enriched
            print("ok")
        except json.JSONDecodeError as e:
            print(f"parse error: {e} — skipping")
        except anthropic.RateLimitError:
            print("rate limited — waiting 10s")
            time.sleep(10)
            try:
                enriched = enrich_row(client, row)
                enriched_map[enriched["id"]] = enriched
                print("ok (retry)")
            except Exception as e2:
                print(f"retry failed: {e2} — skipping")
        except Exception as e:
            print(f"error: {e} — skipping")

        # Flush to disk after every row so progress survives interruption
        output_path.write_text("\n".join(json.dumps(enriched_map[r["id"]]) for r in rows) + "\n")

    print(f"\nWrote {len(rows)} rows to {output_path}")


if __name__ == "__main__":
    main()
