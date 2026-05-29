"""Ingest search score analysis.

Two subcommands:

  analyze      — Score eval samples with BM25 (full content, production
                 settings) and semantic similarity (text-embedding-3-small).
                 Plots score distributions and relevant coverage vs filter level.
                 Scores are cached so re-runs only process new samples.

  paramsearch  — Grid search over BM25 k1 (index-level) and title_boost
                 (query-level) using real OpenSearch. Reports relevant coverage
                 heatmaps at 70/80/90%% filter levels.
                 Note: b (length normalization) has no effect in a
                 single-document-per-query corpus — only k1 and title_boost
                 are worth tuning.

Usage:
    pip install numpy matplotlib

    python scripts/ingest_search_score_analysis.py analyze \\
        --samples eval_samples.jsonl \\
        --cache   score_cache.json \\
        --plot    score_analysis.png

    python scripts/ingest_search_score_analysis.py paramsearch \\
        --samples eval_samples.jsonl \\
        --results param_search_results.json \\
        --plot    param_search.png

Both subcommands accept --os-host / --os-port (default localhost:9201)
and --workers (default 4).

Prerequisites:
    - OpenSearch running locally
    - OPENAI_API_KEY set (analyze only)
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

PROD_K1        = 1.2
PROD_BOOST     = 3.0
EMBED_MODEL    = "text-embedding-3-small"
FILTER_LEVELS  = [50, 60, 70, 75, 80, 85, 90, 95]

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


# -- Data loading -------------------------------------------------------------


def load_records(path: str) -> list[dict]:
    records = []
    with open(path) as f:
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
    return records


# -- OpenSearch helpers -------------------------------------------------------


def make_os_client(host: str, port: int) -> OpenSearch:
    return OpenSearch(hosts=[{"host": host, "port": port}], use_ssl=False)


def ensure_index(client: OpenSearch, index: str, k1: float = PROD_K1) -> None:
    if client.indices.exists(index=index):
        client.indices.delete(index=index)
    client.indices.create(index=index, body={
        "settings": {"index": {"similarity": {"default": {
            "type": "BM25", "k1": k1, "b": 0.75,
        }}}},
        "mappings": {"properties": {
            "path":  {"type": "keyword"},
            "title": {"type": "text"},
            "body":  {"type": "text"},
        }},
    })


def bm25_score_one(
    client: OpenSearch,
    lock: threading.Lock,
    index: str,
    r: dict,
    boost: float = PROD_BOOST,
) -> float:
    with lock:
        client.index(index=index, id=r["path"],
                     body={"path": r["path"], "title": r["title"], "body": r["body"]},
                     refresh=True)
        try:
            resp = client.search(index=index, body={
                "query": {"multi_match": {
                    "query": r["source_content"],
                    "fields": ["title^%.1f" % boost, "body"],
                    "type": "best_fields",
                }},
                "size": 1,
            })
            hits = resp.get("hits", {}).get("hits", [])
            return float(hits[0]["_score"]) if hits else 0.0
        except Exception:
            return 0.0
        finally:
            client.delete(index=index, id=r["path"], refresh=True)


def bm25_score_multi_boost(
    client: OpenSearch,
    lock: threading.Lock,
    index: str,
    r: dict,
    boost_values: list[float],
) -> dict[float, float]:
    with lock:
        client.index(index=index, id=r["path"],
                     body={"path": r["path"], "title": r["title"], "body": r["body"]},
                     refresh=True)
        scores: dict[float, float] = {}
        try:
            for boost in boost_values:
                try:
                    resp = client.search(index=index, body={
                        "query": {"multi_match": {
                            "query": r["source_content"],
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
            client.delete(index=index, id=r["path"], refresh=True)
    return scores


# -- Semantic helpers ---------------------------------------------------------


def cosine_similarity(a: list[float], b: list[float]) -> float:
    va, vb = np.array(a), np.array(b)
    return float(np.dot(va, vb) / (np.linalg.norm(va) * np.linalg.norm(vb)))


def embed(openai_client, text: str) -> list[float]:
    resp = openai_client.embeddings.create(model=EMBED_MODEL, input=text[:30000])
    return resp.data[0].embedding


# -- Plot helpers -------------------------------------------------------------


def dot_plot(ax, rel: list[float], irrel: list[float], title: str) -> None:
    colors = {"relevant": "steelblue", "irrelevant": "tomato"}
    y_pos  = {"relevant": 1, "irrelevant": 0}
    for label, scores in [("relevant", rel), ("irrelevant", irrel)]:
        arr = np.array(scores)
        y = y_pos[label]
        np.random.seed(42)
        ys = y + np.random.uniform(-0.2, 0.2, len(arr))
        median = np.median(arr)
        p25, p75 = np.percentile(arr, 25), np.percentile(arr, 75)
        ax.scatter(arr, ys, color=colors[label], alpha=0.2, s=10,
                   label="%s (n=%d, median=%.3f)" % (label, len(arr), median))
        ax.errorbar(median, y, xerr=[[median - p25], [p75 - median]],
                    fmt="o", color="black", markersize=8,
                    capsize=6, capthick=2, linewidth=2.5, zorder=5)
    ax.set_yticks([0, 1])
    ax.set_yticklabels(["irrelevant", "relevant"], fontsize=12)
    ax.set_xlabel("Score", fontsize=11)
    ax.set_title("%s\nerror bar = median +/- IQR" % title, fontsize=12)
    ax.legend(loc="upper right", fontsize=10)
    ax.grid(True, axis="x", alpha=0.3)


def coverage_at(rel: list[float], irrel: list[float], pct: float) -> float:
    t = np.percentile(irrel, pct)
    return sum(1 for s in rel if s >= t) / len(rel) * 100


# -- Subcommand: analyze ------------------------------------------------------


def cmd_analyze(args: argparse.Namespace) -> None:
    from openai import OpenAI
    openai_client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])

    print("Loading data...")
    records = load_records(args.samples)
    print(f"Loaded {len(records)} records")

    cache: dict[str, dict] = {}
    if os.path.exists(args.cache):
        with open(args.cache) as f:
            cache = json.load(f)
        print(f"Loaded {len(cache)} cached scores")

    os_client = make_os_client(args.os_host, args.os_port)
    index = "ingest-score-analysis"
    ensure_index(os_client, index)
    os_lock = threading.Lock()

    def process(r: dict) -> tuple[str, dict]:
        rid = r["id"]
        cached = cache.get(rid, {})

        b = cached.get("bm25_score")
        if b is None:
            b = bm25_score_one(os_client, os_lock, index, r)

        s = cached.get("semantic_score")
        if s is None:
            try:
                s = cosine_similarity(
                    embed(openai_client, r["source_content"]),
                    embed(openai_client, r["body"]),
                )
            except Exception as e:
                print(f"  [warn] embed failed for {rid}: {e}")
                s = 0.0

        return rid, {
            "label": r["label"],
            "wiki_path": r["path"],
            "bm25_score": round(b, 6),
            "semantic_score": round(s, 6),
        }

    needs = [r for r in records
             if r["id"] not in cache
             or "bm25_score" not in cache.get(r["id"], {})
             or "semantic_score" not in cache.get(r["id"], {})]
    print(f"Records needing scoring: {len(needs)}")

    done = 0
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futures = {ex.submit(process, r): r for r in needs}
        for future in as_completed(futures):
            rid, result = future.result()
            cache[rid] = result
            done += 1
            if done % 200 == 0:
                print(f"  {done}/{len(needs)}")
                with open(args.cache, "w") as f:
                    json.dump(cache, f, indent=2)

    with open(args.cache, "w") as f:
        json.dump(cache, f, indent=2)
    print(f"Saved {len(cache)} scores to {args.cache}")

    bm25_rel, bm25_irrel, sem_rel, sem_irrel = [], [], [], []
    for v in cache.values():
        if not v.get("label"):
            continue
        if v["label"] == "relevant":
            bm25_rel.append(v["bm25_score"])
            sem_rel.append(v["semantic_score"])
        else:
            bm25_irrel.append(v["bm25_score"])
            sem_irrel.append(v["semantic_score"])

    print(f"\nrelevant={len(bm25_rel)}  irrelevant={len(bm25_irrel)}")
    print("\n-- Coverage at filter levels --")
    print("%-22s  " % "irrel filtered %" + "  ".join("%6d%%" % l for l in FILTER_LEVELS))
    print("-" * 90)
    for name, rel, irrel in [("BM25 full content", bm25_rel, bm25_irrel),
                              ("Semantic similarity", sem_rel, sem_irrel)]:
        row = "%-22s  " % name
        row += "  ".join("%6.1f%%" % coverage_at(rel, irrel, pct) for pct in FILTER_LEVELS)
        print(row)

    fig, axes = plt.subplots(3, 1, figsize=(14, 13))
    dot_plot(axes[0], bm25_rel, bm25_irrel, "BM25 Full Content")
    dot_plot(axes[1], sem_rel, sem_irrel, "Semantic Similarity (text-embedding-3-small)")

    ax3 = axes[2]
    for name, rel, irrel, color, marker in [
        ("BM25 full content",   bm25_rel, bm25_irrel, "steelblue", "o"),
        ("Semantic similarity", sem_rel,  sem_irrel,  "darkorange", "s"),
    ]:
        coverages = [coverage_at(rel, irrel, pct) for pct in FILTER_LEVELS]
        ax3.plot(FILTER_LEVELS, coverages, color=color, marker=marker,
                 linewidth=2, markersize=7, label=name)
        for x, y in zip(FILTER_LEVELS, coverages):
            ax3.annotate("%.1f%%" % y, (x, y), textcoords="offset points",
                         xytext=(0, 8), ha="center", fontsize=8, color=color)
    ax3.set_xlabel("Irrelevant filtered (%)", fontsize=11)
    ax3.set_ylabel("Relevant coverage (%)", fontsize=11)
    ax3.set_title("Relevant coverage at each filter level", fontsize=12)
    ax3.set_xticks(FILTER_LEVELS)
    ax3.set_xticklabels(["%d%%" % l for l in FILTER_LEVELS])
    ax3.set_ylim(40, 105)
    ax3.legend(fontsize=10)
    ax3.grid(True, alpha=0.3)

    n_total = len(bm25_rel) + len(bm25_irrel)
    fig.suptitle(
        "Ingest Search Score Distribution  |  n=%d  |  %s" % (
            n_total, os.path.basename(args.samples)),
        fontsize=13,
    )
    plt.tight_layout()
    plt.savefig(args.plot, dpi=150, bbox_inches="tight")
    print(f"Plot saved to {args.plot}")


# -- Subcommand: paramsearch --------------------------------------------------


def cmd_paramsearch(args: argparse.Namespace) -> None:
    k1_values    = [0.5, 0.8, 1.0, 1.2, 1.5, 2.0, 2.5, 3.0]
    boost_values = [1.0, 2.0, 3.0, 5.0, 8.0]
    plot_levels  = [70, 80, 90]

    print("Loading data...")
    records = load_records(args.samples)
    print(f"Loaded {len(records)} records")

    all_results: dict[str, dict] = {}
    if os.path.exists(args.results):
        with open(args.results) as f:
            all_results = json.load(f)
        print(f"Cached k1 values: {list(all_results.keys())}")

    client = make_os_client(args.os_host, args.os_port)
    index = "ingest-param-search"

    for k1 in k1_values:
        k1_key = str(k1)
        if k1_key in all_results:
            print(f"k1={k1}: using cached results")
            continue

        print(f"\nk1={k1}: recreating index, scoring {len(records)} records...")
        ensure_index(client, index, k1=k1)
        lock = threading.Lock()

        scores_by_boost: dict[float, dict[str, list[float]]] = {
            b: {"relevant": [], "irrelevant": []} for b in boost_values
        }

        done = 0
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            futures = {ex.submit(bm25_score_multi_boost, client, lock, index, r, boost_values): r
                       for r in records}
            for future in as_completed(futures):
                r = futures[future]
                for boost, score in future.result().items():
                    scores_by_boost[boost][r["label"]].append(score)
                done += 1
                if done % 500 == 0:
                    print(f"  {done}/{len(records)}")

        k1_result: dict[str, dict[str, float]] = {}
        for boost in boost_values:
            rel   = scores_by_boost[boost]["relevant"]
            irrel = scores_by_boost[boost]["irrelevant"]
            k1_result[str(boost)] = {
                str(pct): round(coverage_at(rel, irrel, pct), 2)
                for pct in plot_levels
            }

        all_results[k1_key] = k1_result
        with open(args.results, "w") as f:
            json.dump(all_results, f, indent=2)
        print(f"k1={k1}: saved")

    if client.indices.exists(index=index):
        client.indices.delete(index=index)

    print("\n-- Coverage at 70%% filter --")
    print("%-6s  " % "k1" + "  ".join("%8s" % ("b=%.1f" % b) for b in boost_values))
    print("-" * 60)
    for k1 in k1_values:
        row = "%-6s  " % k1
        for boost in boost_values:
            cov = all_results[str(k1)][str(boost)]["70"]
            marker = "*" if k1 == PROD_K1 and boost == PROD_BOOST else " "
            row += "%7.1f%%%s " % (cov, marker)
        print(row)

    best_cov, best_k1, best_boost = 0.0, None, None
    for k1 in k1_values:
        for boost in boost_values:
            cov = all_results[str(k1)][str(boost)]["70"]
            if cov > best_cov:
                best_cov, best_k1, best_boost = cov, k1, boost
    prod_cov = all_results[str(PROD_K1)][str(PROD_BOOST)]["70"]
    print(f"\nBest at 70%% filter: k1={best_k1}  boost={best_boost}  coverage={best_cov:.1f}%%")
    print(f"Production (k1={PROD_K1}, boost={PROD_BOOST}): {prod_cov:.1f}%%")

    fig, axes = plt.subplots(1, len(plot_levels), figsize=(18, 6))
    for ax, pct in zip(axes, plot_levels):
        data = np.array([
            [all_results[str(k1)][str(b)][str(pct)] for b in boost_values]
            for k1 in k1_values
        ])
        im = ax.imshow(data, aspect="auto", cmap="RdYlGn", vmin=50, vmax=100)
        plt.colorbar(im, ax=ax, label="Relevant coverage (%)")
        ax.set_xticks(range(len(boost_values)))
        ax.set_xticklabels(boost_values)
        ax.set_yticks(range(len(k1_values)))
        ax.set_yticklabels(k1_values)
        ax.set_xlabel("Title boost", fontsize=11)
        ax.set_ylabel("k1", fontsize=11)
        ax.set_title("Filter %d%% irrelevant\nRelevant coverage %%" % pct, fontsize=12)
        for i in range(len(k1_values)):
            for j in range(len(boost_values)):
                val = data[i][j]
                color = "black" if 60 < val < 90 else "white"
                ax.text(j, i, "%.1f" % val, ha="center", va="center", fontsize=8, color=color)
        prod_i = k1_values.index(PROD_K1)
        prod_j = boost_values.index(PROD_BOOST)
        ax.add_patch(plt.Rectangle(
            (prod_j - 0.5, prod_i - 0.5), 1, 1,
            fill=False, edgecolor="blue", linewidth=2.5,
        ))

    fig.suptitle(
        "BM25 Parameter Grid Search  |  n=%d  |  blue = production (k1=%.1f, boost=%.1f)" % (
            len(records), PROD_K1, PROD_BOOST),
        fontsize=13,
    )
    plt.tight_layout()
    plt.savefig(args.plot, dpi=150, bbox_inches="tight")
    print(f"Plot saved to {args.plot}")


# -- Entry point --------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--os-host", default="localhost")
    parser.add_argument("--os-port", type=int, default=9201)
    parser.add_argument("--workers", type=int, default=4)

    sub = parser.add_subparsers(dest="cmd", required=True)

    p_analyze = sub.add_parser("analyze", help="BM25 + semantic score distributions")
    p_analyze.add_argument("--samples", required=True)
    p_analyze.add_argument("--cache",   required=True, help="Score cache JSON (read/write)")
    p_analyze.add_argument("--plot",    required=True)

    p_param = sub.add_parser("paramsearch", help="BM25 k1 / title_boost grid search")
    p_param.add_argument("--samples", required=True)
    p_param.add_argument("--results", required=True, help="Results JSON (read/write)")
    p_param.add_argument("--plot",    required=True)

    args = parser.parse_args()
    if args.cmd == "analyze":
        cmd_analyze(args)
    else:
        cmd_paramsearch(args)


if __name__ == "__main__":
    main()
