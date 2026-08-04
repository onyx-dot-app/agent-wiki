"""Vector helpers shared by the derivation stages that group things by similarity.

Extracted from ``app.ingest.entity_types`` when need clustering became a second consumer. The
functions are unchanged; only the home is new, so both callers use one implementation rather than
one importing the other's privates.
"""

from __future__ import annotations

import math


def normalize(vec: list[float]) -> list[float]:
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


def cosine(a: list[float], b: list[float]) -> float:
    """Both operands are unit vectors, so the dot product IS the cosine."""
    return sum(x * y for x, y in zip(a, b))


def leader_cluster(
    vectors: list[list[float]], order: list[int], threshold: float
) -> list[list[int]]:
    """Greedy leader clustering: each item joins the first cluster whose centroid is within
    ``threshold``, else seeds a new one.

    Chosen over agglomerative linkage because the backend has no scipy/sklearn and an O(n^3)
    pure-Python linkage would not finish on a real corpus. ``order`` should put the
    best-supported items first so they seed clusters — that makes the result deterministic and
    puts the strongest evidence in charge of each group.

    A threshold tuned for this algorithm does NOT transfer to a linkage method: the centroid
    drifts toward a generic average as a cluster grows and then admits almost anything, so it
    tolerates far less permissiveness at the "same" similarity.
    """
    clusters: list[list[int]] = []
    centroids: list[list[float]] = []
    for idx in order:
        vec = vectors[idx]
        best, best_sim = -1, threshold
        for c, centroid in enumerate(centroids):
            sim = cosine(vec, centroid)
            if sim >= best_sim:
                best, best_sim = c, sim
        if best < 0:
            clusters.append([idx])
            centroids.append(list(vec))
            continue
        members = clusters[best]
        centroid = centroids[best]
        n = len(members)
        centroids[best] = normalize([(c * n + v) / (n + 1) for c, v in zip(centroid, vec)])
        members.append(idx)
    return clusters
