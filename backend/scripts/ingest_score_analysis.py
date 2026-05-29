"""Relevance score analysis for the ingest BM25 filter.

Compares BM25 full-content scoring and semantic cosine similarity
(text-embedding-3-small) across two groups — relevant (committed +
no_change) and irrelevant — using a labeled eval sample file.

Scores are cached so re-runs only process new records. Produces a
three-panel plot: BM25 dot distribution, semantic dot distribution,
and a coverage-vs-filter-level curve comparing both approaches.

Usage:
    cd backend
    uv run --extra dev python scripts/ingest_score_analysis.py \\
        --samples path/to/eval_samples.jsonl \\
        --cache   path/to/score_cache.json \\
        --plot    path/to/output.png

Prerequisites:
    - OpenSearch running (default localhost:9201, override with --os-host/--os-port)
    - OPENAI_API_KEY set in environment
    - uv sync --extra dev
"""

from __future__ import annotations

import argparse
import json
import math
import os
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

import matplotlib
import numpy as np

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from openai import OpenAI
from opensearchpy import OpenSearch

EMBED_MODEL = "text-embedding-3-small"
INDEX = "bm25-score-analysis"


# -- Label helpers ------------------------------------------------------------


def normalize_label(label: str) -> str | None:
    if label in (
        "committed",
        "committed_moderate",
        "committed_critical",
        "committed_nit",
        "no_change",
        "no_change_covered",
        "no_change_extra",
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


# -- BM25 ---------------------------------------------------------------------


def make_os_client(host: str, port: int) -> OpenSearch:
    return OpenSearch(hosts=[{"host": host, "port": port}], use_ssl=False)


def ensure_index(client: OpenSearch) -> None:
    if not client.indices.exists(index=INDEX):
        client.indices.create(
            index=INDEX,
            body={
                "settings": {
                    "index": {
                        "similarity": {
                            "default": {"type": "BM25", "b": 0.75, "k1": 1.2}
                        }
                    }
                },
                "mappings": {
                    "properties": {
                        "path": {"type": "keyword"},
                        "title": {"type": "text", "boost": 3.0},
                        "body": {"type": "text"},
                    }
                },
            },
        )


def bm25_score(
    client: OpenSearch,
    lock: threading.Lock,
    query: str,
    path: str,
    title: str,
    body: str,
) -> float:
    with lock:
        client.index(
            index=INDEX,
            id=path,
            body={"path": path, "title": title, "body": body},
            refresh=True,
        )
        try:
            resp = client.search(
                index=INDEX,
                body={
                    "query": {
                        "multi_match": {
                            "query": query,
                            "fields": ["title^3", "body"],
                            "type": "best_fields",
                        }
                    },
                    "size": 1,
                },
            )
            hits = resp.get("hits", {}).get("hits", [])
            return float(hits[0]["_score"]) if hits else 0.0
        except Exception:
            return 0.0
        finally:
            client.delete(index=INDEX, id=path, refresh=True)


# -- Semantic -----------------------------------------------------------------


def cosine_similarity(a: list[float], b: list[float]) -> float:
    va, vb = np.array(a), np.array(b)
    return float(np.dot(va, vb) / (np.linalg.norm(va) * np.linalg.norm(vb)))


def embed(openai_client: OpenAI, text: str) -> list[float]:
    resp = openai_client.embeddings.create(model=EMBED_MODEL, input=text[:30000])
    return resp.data[0].embedding


# -- Main ---------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", required=True, help="Labeled eval JSONL file")
    parser.add_argument("--cache", required=True, help="Score cache JSON (read/write)")
    parser.add_argument("--plot", required=True, help="Output plot PNG path")
    parser.add_argument("--os-host", default="localhost")
    parser.add_argument("--os-port", type=int, default=9201)
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()

    openai_client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    os_client = make_os_client(args.os_host, args.os_port)
    ensure_index(os_client)
    os_lock = threading.Lock()

    print("Loading data...")
    records = []
    with open(args.samples) as f:
        for line in f:
            r = json.loads(line)
            label = resolve_label(r)
            if label and r.get("source_content") and r.get("wiki_body_before"):
                records.append(r)
    print(f"Loaded {len(records)} labeled records")

    cache: dict[str, dict] = {}
    if os.path.exists(args.cache):
        with open(args.cache) as f:
            cache = json.load(f)
        print(f"Loaded {len(cache)} cached scores")

    def process(r: dict) -> tuple[str, dict]:
        rid = str(r["id"])
        label = resolve_label(r)
        source = r["source_content"]
        path = r["wiki_path"]
        title = path.split("/")[-1].replace(".md", "")
        body = r["wiki_body_before"]
        cached = cache.get(rid, {})

        b = cached.get("bm25_score")
        if b is None:
            b = bm25_score(os_client, os_lock, source, path, title, body)

        s = cached.get("semantic_score")
        if s is None:
            try:
                s = cosine_similarity(embed(openai_client, source), embed(openai_client, body))
            except Exception as e:
                print(f"  [warn] embed failed for {rid}: {e}")
                s = 0.0

        return rid, {
            "label": label,
            "wiki_path": path,
            "bm25_score": round(b, 6),
            "semantic_score": round(s, 6),
        }

    needs = [
        r for r in records
        if str(r["id"]) not in cache
        or "bm25_score" not in cache.get(str(r["id"]), {})
        or "semantic_score" not in cache.get(str(r["id"]), {})
    ]
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

    # -- Groups ---------------------------------------------------------------

    bm25_rel, bm25_irrel = [], []
    sem_rel, sem_irrel = [], []
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

    # -- Stats ----------------------------------------------------------------

    filter_levels = [50, 60, 70, 75, 80, 85, 90, 95]
    for name, rel, irrel in [("BM25", bm25_rel, bm25_irrel), ("Semantic", sem_rel, sem_irrel)]:
        print(f"\n-- {name} --")
        print("%-12s  %6s  %6s  %6s" % ("group", "n", "mean", "median"))
        print("%-12s  %6d  %6.3f  %6.3f" % ("relevant", len(rel), np.mean(rel), np.median(rel)))
        print("%-12s  %6d  %6.3f  %6.3f" % ("irrelevant", len(irrel), np.mean(irrel), np.median(irrel)))

    print("\n-- Coverage at filter levels --")
    print("%-22s  " % "irrel filtered %" + "  ".join("%6d%%" % l for l in filter_levels))
    print("-" * 90)
    for name, rel, irrel in [("BM25 full content", bm25_rel, bm25_irrel),
                              ("Semantic similarity", sem_rel, sem_irrel)]:
        coverages = []
        for pct in filter_levels:
            t = np.percentile(irrel, pct)
            coverages.append("%6.1f%%" % (sum(1 for s in rel if s >= t) / len(rel) * 100))
        print("%-22s  " % name + "  ".join(coverages))

    # -- Plot -----------------------------------------------------------------

    colors = {"relevant": "steelblue", "irrelevant": "tomato"}
    y_pos = {"relevant": 1, "irrelevant": 0}
    fig, axes = plt.subplots(3, 1, figsize=(14, 13))

    for ax, (name, rel, irrel) in zip(axes[:2], [
        ("BM25 Full Content", bm25_rel, bm25_irrel),
        ("Semantic Similarity (text-embedding-3-small)", sem_rel, sem_irrel),
    ]):
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
        ax.set_title("%s\nerror bar = median +/- IQR" % name, fontsize=12)
        ax.legend(loc="upper right", fontsize=10)
        ax.grid(True, axis="x", alpha=0.3)

    ax3 = axes[2]
    for name, rel, irrel, color, marker in [
        ("BM25 full content", bm25_rel, bm25_irrel, "steelblue", "o"),
        ("Semantic similarity", sem_rel, sem_irrel, "darkorange", "s"),
    ]:
        coverages = []
        for pct in filter_levels:
            t = np.percentile(irrel, pct)
            coverages.append(sum(1 for s in rel if s >= t) / len(rel) * 100)
        ax3.plot(filter_levels, coverages, color=color, marker=marker,
                 linewidth=2, markersize=7, label=name)
        for x, y in zip(filter_levels, coverages):
            ax3.annotate("%.1f%%" % y, (x, y), textcoords="offset points",
                         xytext=(0, 8), ha="center", fontsize=8, color=color)

    ax3.set_xlabel("Irrelevant filtered (%)", fontsize=11)
    ax3.set_ylabel("Relevant coverage (%)", fontsize=11)
    ax3.set_title("Relevant coverage at each filter level", fontsize=12)
    ax3.set_xticks(filter_levels)
    ax3.set_xticklabels(["%d%%" % l for l in filter_levels])
    ax3.set_ylim(40, 105)
    ax3.legend(fontsize=10)
    ax3.grid(True, alpha=0.3)

    n_total = len(bm25_rel) + len(bm25_irrel)
    fig.suptitle(
        "Relevance Score Distribution  |  n=%d  |  %s" % (n_total, os.path.basename(args.samples)),
        fontsize=13,
    )
    plt.tight_layout()
    plt.savefig(args.plot, dpi=150, bbox_inches="tight")
    print(f"Plot saved to {args.plot}")


if __name__ == "__main__":
    main()
