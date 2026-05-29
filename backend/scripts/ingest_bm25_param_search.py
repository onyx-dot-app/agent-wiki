"""BM25 parameter grid search for the ingest filter.

Grids over k1 (index-level) and title_boost (query-level) using real
OpenSearch with the same BM25 settings as production. For each combination,
scores all eval samples and reports relevant coverage at 70/80/90% filter
levels.

Note: b (length normalization) has no effect in a single-document-per-query
corpus because avgdl == dl always, so b * dl/avgdl = b * 1 = constant.
Only k1 and title_boost are worth tuning.

Results are cached per k1 value so interrupted runs resume from where they
left off. Each k1 value requires recreating the OpenSearch index; title_boost
varies at query time with no index rebuild.

Usage:
    cd backend
    uv run --extra dev python scripts/ingest_bm25_param_search.py \\
        --samples path/to/eval_samples.jsonl \\
        --results path/to/param_search_results.json \\
        --plot    path/to/output.png

Prerequisites:
    - OpenSearch running (default localhost:9201, override with --os-host/--os-port)
    - uv sync --extra dev
"""

from __future__ import annotations

import argparse
import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

import matplotlib
import numpy as np

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from opensearchpy import OpenSearch

INDEX = "bm25-param-search"

DEFAULT_K1_VALUES    = [0.5, 0.8, 1.0, 1.2, 1.5, 2.0, 2.5, 3.0]
DEFAULT_BOOST_VALUES = [1.0, 2.0, 3.0, 5.0, 8.0]
FILTER_LEVELS        = [70, 80, 90]
PROD_K1              = 1.2
PROD_BOOST           = 3.0


# -- Label helpers ------------------------------------------------------------


def normalize_label(label: str) -> str | None:
    if label in (
        "committed", "committed_moderate", "committed_critical", "committed_nit",
        "no_change", "no_change_covered", "no_change_extra",
    ):
        return "relevant"
    if label == "irrelevant":
        return "irrelevant"
    return None


def resolve_label(r: dict) -> str | None:
    for field in ("label", "judge_label", "outcome"):
        v = r.get(field)
        if v:
            n = normalize_label(v)
            if n:
                return n
    return None


# -- OpenSearch helpers -------------------------------------------------------


def make_client(host: str, port: int) -> OpenSearch:
    return OpenSearch(hosts=[{"host": host, "port": port}], use_ssl=False)


def recreate_index(client: OpenSearch, k1: float) -> None:
    if client.indices.exists(index=INDEX):
        client.indices.delete(index=INDEX)
    client.indices.create(
        index=INDEX,
        body={
            "settings": {"index": {"similarity": {"default": {
                "type": "BM25", "k1": k1, "b": 0.75,
            }}}},
            "mappings": {"properties": {
                "path":  {"type": "keyword"},
                "title": {"type": "text"},
                "body":  {"type": "text"},
            }},
        },
    )


def score_record(
    client: OpenSearch,
    lock: threading.Lock,
    r: dict,
    boost_values: list[float],
) -> dict[float, float]:
    path, title, body, query = r["path"], r["title"], r["body"], r["source_content"]
    scores: dict[float, float] = {}
    with lock:
        client.index(index=INDEX, id=path,
                     body={"path": path, "title": title, "body": body},
                     refresh=True)
        try:
            for boost in boost_values:
                try:
                    resp = client.search(index=INDEX, body={
                        "query": {"multi_match": {
                            "query": query,
                            "fields": ["title^%.1f" % boost, "body"],
                            "type": "best_fields",
                        }},
                        "size": 1,
                    })
                    hits = resp.get("hits", {}).get("hits", [])
                    scores[boost] = float(hits[0]["_score"]) if hits else 0.0
                except Exception:
                    scores[boost] = 0.0
        finally:
            client.delete(index=INDEX, id=path, refresh=True)
    return scores


# -- Main ---------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", required=True, help="Labeled eval JSONL file")
    parser.add_argument("--results", required=True, help="Results JSON (read/write)")
    parser.add_argument("--plot", required=True, help="Output plot PNG path")
    parser.add_argument("--os-host", default="localhost")
    parser.add_argument("--os-port", type=int, default=9201)
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()

    print("Loading data...")
    records = []
    with open(args.samples) as f:
        for line in f:
            r = json.loads(line)
            label = resolve_label(r)
            if label and r.get("source_content") and r.get("wiki_body_before"):
                records.append({
                    "id": str(r["id"]),
                    "label": label,
                    "source_content": r["source_content"],
                    "title": r["wiki_path"].split("/")[-1].replace(".md", ""),
                    "body": r["wiki_body_before"],
                    "path": r["wiki_path"],
                })
    print(f"Loaded {len(records)} records")

    all_results: dict[str, dict] = {}
    if os.path.exists(args.results):
        with open(args.results) as f:
            all_results = json.load(f)
        print(f"Cached k1 values: {list(all_results.keys())}")

    client = make_client(args.os_host, args.os_port)

    for k1 in DEFAULT_K1_VALUES:
        k1_key = str(k1)
        if k1_key in all_results:
            print(f"k1={k1}: using cached results")
            continue

        print(f"\nk1={k1}: recreating index, scoring {len(records)} records...")
        recreate_index(client, k1)
        lock = threading.Lock()

        scores_by_boost: dict[float, dict[str, list[float]]] = {
            b: {"relevant": [], "irrelevant": []} for b in DEFAULT_BOOST_VALUES
        }

        done = 0
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            futures = {ex.submit(score_record, client, lock, r, DEFAULT_BOOST_VALUES): r
                       for r in records}
            for future in as_completed(futures):
                r = futures[future]
                for boost, score in future.result().items():
                    scores_by_boost[boost][r["label"]].append(score)
                done += 1
                if done % 500 == 0:
                    print(f"  {done}/{len(records)}")

        k1_result: dict[str, dict[str, float]] = {}
        for boost in DEFAULT_BOOST_VALUES:
            rel   = scores_by_boost[boost]["relevant"]
            irrel = scores_by_boost[boost]["irrelevant"]
            k1_result[str(boost)] = {}
            for pct in FILTER_LEVELS:
                t = np.percentile(irrel, pct)
                coverage = sum(1 for s in rel if s >= t) / len(rel) * 100
                k1_result[str(boost)][str(pct)] = round(coverage, 2)

        all_results[k1_key] = k1_result
        with open(args.results, "w") as f:
            json.dump(all_results, f, indent=2)
        print(f"k1={k1}: saved")

    if client.indices.exists(index=INDEX):
        client.indices.delete(index=INDEX)

    # -- Summary --------------------------------------------------------------

    print("\n-- Coverage at 70%% filter --")
    print("%-6s  " % "k1" + "  ".join("%8s" % ("b=%.1f" % b) for b in DEFAULT_BOOST_VALUES))
    print("-" * 60)
    for k1 in DEFAULT_K1_VALUES:
        row = "%-6s  " % k1
        for boost in DEFAULT_BOOST_VALUES:
            cov = all_results[str(k1)][str(boost)]["70"]
            marker = "*" if k1 == PROD_K1 and boost == PROD_BOOST else " "
            row += "%7.1f%%%s " % (cov, marker)
        print(row)

    best_cov, best_k1, best_boost = 0.0, None, None
    for k1 in DEFAULT_K1_VALUES:
        for boost in DEFAULT_BOOST_VALUES:
            cov = all_results[str(k1)][str(boost)]["70"]
            if cov > best_cov:
                best_cov, best_k1, best_boost = cov, k1, boost
    prod_cov = all_results[str(PROD_K1)][str(PROD_BOOST)]["70"]
    print(f"\nBest at 70%% filter: k1={best_k1}  boost={best_boost}  coverage={best_cov:.1f}%%")
    print(f"Production (k1={PROD_K1}, boost={PROD_BOOST}): {prod_cov:.1f}%%")

    # -- Heatmaps -------------------------------------------------------------

    fig, axes = plt.subplots(1, len(FILTER_LEVELS), figsize=(18, 6))
    for ax, pct in zip(axes, FILTER_LEVELS):
        data = np.array([
            [all_results[str(k1)][str(b)][str(pct)] for b in DEFAULT_BOOST_VALUES]
            for k1 in DEFAULT_K1_VALUES
        ])
        im = ax.imshow(data, aspect="auto", cmap="RdYlGn", vmin=50, vmax=100)
        plt.colorbar(im, ax=ax, label="Relevant coverage (%)")
        ax.set_xticks(range(len(DEFAULT_BOOST_VALUES)))
        ax.set_xticklabels(DEFAULT_BOOST_VALUES)
        ax.set_yticks(range(len(DEFAULT_K1_VALUES)))
        ax.set_yticklabels(DEFAULT_K1_VALUES)
        ax.set_xlabel("Title boost", fontsize=11)
        ax.set_ylabel("k1", fontsize=11)
        ax.set_title("Filter %d%% irrelevant\nRelevant coverage %%" % pct, fontsize=12)
        for i in range(len(DEFAULT_K1_VALUES)):
            for j in range(len(DEFAULT_BOOST_VALUES)):
                val = data[i][j]
                color = "black" if 60 < val < 90 else "white"
                ax.text(j, i, "%.1f" % val, ha="center", va="center", fontsize=8, color=color)
        prod_i = DEFAULT_K1_VALUES.index(PROD_K1)
        prod_j = DEFAULT_BOOST_VALUES.index(PROD_BOOST)
        ax.add_patch(plt.Rectangle(
            (prod_j - 0.5, prod_i - 0.5), 1, 1,
            fill=False, edgecolor="blue", linewidth=2.5,
        ))

    fig.suptitle(
        "BM25 parameter grid  |  n=%d  |  blue = production (k1=%.1f, boost=%.1f)" % (
            len(records), PROD_K1, PROD_BOOST),
        fontsize=13,
    )
    plt.tight_layout()
    plt.savefig(args.plot, dpi=150, bbox_inches="tight")
    print(f"Plot saved to {args.plot}")


if __name__ == "__main__":
    main()
