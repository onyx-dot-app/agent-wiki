"""Derive the entity-type taxonomy from this deployment's wiki and write the artifact.

Offline and one-shot, like the other scripts here — not a request-path or worker concern.
The taxonomy describes what kinds of thing this wiki tracks (organizations, people, software
products, …) and is derived from the pages themselves, so no one has to author a type list
for their deployment.

Freeze it. Two of the derivation's stages are LLM calls, so re-running can rename a type,
and anything keyed by the old name is orphaned. Treat the output as an input from then on
and re-derive deliberately, not on a schedule. ``corpus_fingerprint`` and ``derived_at`` in
the artifact are there to tell you whether a re-derivation is warranted and what it changed.

If no artifact exists, ``entity_types.load_taxonomy`` falls back to a small generic type
list — the same degradation as the relevance scorer without its model file. Nothing breaks;
the types are just not tailored to the corpus.

    python -m app.scripts.derive_entity_types --out /data/entity_types.json
    python -m app.scripts.derive_entity_types --out - --limit 20   # smoke test to stdout
"""

from __future__ import annotations

import argparse
import json
import logging
import sys

from app.ingest import entity_types

log = logging.getLogger(__name__)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Derive an entity-type taxonomy from the wiki corpus."
    )
    parser.add_argument(
        "--out",
        required=True,
        help="artifact path, or '-' for stdout",
    )
    parser.add_argument(
        "--prefix",
        default="",
        help="restrict to pages under this path prefix",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="only the first N pages (smoke test; a partial corpus gives a partial taxonomy)",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="override the LLM used for extraction, naming, and merging",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)

    pages = entity_types.read_corpus(args.prefix)
    if args.limit:
        pages = pages[: args.limit]
    if not pages:
        print("no wiki pages found — nothing to derive from", file=sys.stderr)
        return 1

    chars = sum(len(b) for _, b in pages)
    print(f"deriving from {len(pages)} page(s), {chars:,} chars", file=sys.stderr)

    def progress(stage: str, done: int, total: int) -> None:
        if done == total or done % 20 == 0:
            print(f"  {stage}: {done}/{total}", file=sys.stderr)

    try:
        artifact = entity_types.derive(pages, model=args.model, progress=progress)
    except RuntimeError as exc:
        print(f"derivation failed: {exc}", file=sys.stderr)
        return 1

    stats = artifact["stats"]
    print(
        f"\n{stats['n_mentions']:,} mentions -> {stats['n_referents']:,} referents "
        f"-> {stats['n_kept']} kept -> {stats['n_types']} types",
        file=sys.stderr,
    )
    if stats["ambient"]:
        print(f"ambient (no discriminative signal): {', '.join(stats['ambient'])}", file=sys.stderr)
    for t in artifact["entity_types"]:
        print(f"  {t['n_referents']:>5} ref  {t['name']}", file=sys.stderr)

    payload = json.dumps(artifact, ensure_ascii=False, indent=2)
    if args.out == "-":
        print(payload)
    else:
        with open(args.out, "w", encoding="utf-8") as handle:
            handle.write(payload + "\n")
        print(f"\nwrote {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
