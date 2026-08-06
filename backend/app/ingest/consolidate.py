"""Consolidate per-page needs into the need map — topics, their aspects, and the needs composing
each aspect.

``topics.cluster_needs`` finds which needs *might* belong together, by embedding. It cannot say
what they are, and it is imprecise in a specific way: it pulls together needs that merely share a
subject area. The widest real cluster held 19 needs across 18 pages joined only by being *about
Agent Wiki* — a subject, not a facet. This step imposes the structure the embedding could not,
with one LLM call per cluster that does two things at once:

    partition by SUBJECT   -> topics    (splitting a cluster the embedding over-merged)
    group by FACET         -> aspects   (the unit of fan-out)

Both levels come from one call because they need the same context — you cannot decide which
needs share a facet without first deciding which share a subject, and re-supplying the cluster to
a second call would pay for the same tokens twice.

The partition half is measured: one call over that 19-need cluster returned 11 topics with 19/19
coverage, including an ``implementation status`` aspect spanning two pages that the embedding had
buried. The aspect half runs here for the first time.

Deliberately NOT here: the current STATE of each aspect. It changes with every relevant document
while the need map is a snapshot, so it belongs in its own current-valued store — and keying it
needs an aspect identity that survives re-derivation, which does not exist yet.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Any, NamedTuple, cast

from app.db import need_map, page_needs
from app.ingest import entity_types, json_completion, topics
from app.llm import embeddings
from app.llm.settings import get as get_llm_settings
from app.llm.prompts import load_prompt

log = logging.getLogger(__name__)

# One call per cluster, and the calls are independent. Entity-type derivation learned this the
# expensive way: run sequentially, 147 pages took ~1.5 hours in production and a pod restart
# killed it before it recorded anything. Same number as that step, for the same reason.
DEFAULT_WORKERS = 8

# A cluster larger than this is named in one call anyway, but says so in the log. The listing is
# one short line per need, so the input is small; the risk at size is the OUTPUT, which must
# enumerate every index. At ~30 tokens per need the 16k cap covers several hundred.
LARGE_CLUSTER = 120


class AspectDraft(NamedTuple):
    """One facet, with the needs composing it — before it is written."""

    name: str
    description: str
    members: list[topics.NeedRef]


class TopicDraft(NamedTuple):
    """One subject and its facets."""

    name: str
    description: str
    aspects: list[AspectDraft]


def _listing(members: list[topics.NeedRef]) -> str:
    """The cluster as the model sees it: one line per need, numbered from 1.

    The page is named because two needs with the same wording on different pages are the fan-out
    this step exists to find — without it the model cannot tell one page's need restated from two
    pages tracking the same thing. ``current_content`` is left out: it is the state, it is by far
    the largest field, and what a need TRACKS is what decides where it belongs.
    """
    lines: list[str] = []
    for i, ref in enumerate(members, start=1):
        title = ref.path.rsplit("/", 1)[-1].removesuffix(".md")
        name = str(ref.need.get("need_name") or "").strip()
        description = str(ref.need.get("description") or "").strip()
        lines.append(f"[{i}] page={title}\n     tracks: {name}" + (f" — {description}" if description else ""))
    return "\n".join(lines)


def name_cluster(
    cluster: topics.Cluster, *, model: str | None = None
) -> list[TopicDraft]:
    """Turn one cluster into topics and their aspects. Empty when the call fails.

    Empty rather than a fallback guess: a cluster the model could not structure is a cluster we
    know nothing about, and inventing a topic named after its first member would put a confident
    wrong row in the map. The caller counts what was lost.
    """
    members = cluster.members
    if not members:
        return []
    if len(members) > LARGE_CLUSTER:
        log.info(
            "consolidate: naming an unusually large cluster of %d need(s) across %d page(s)",
            len(members),
            len(cluster.pages),
        )

    data = json_completion.complete_json(
        load_prompt("need_map.name"),
        f"Needs in this group:\n\n{_listing(members)}",
        model=model,
        ctx=f"a cluster of {len(members)} need(s) across {len(cluster.pages)} page(s)",
        module="consolidate",
    )

    # The prompt requires a partition: every need in exactly one aspect of exactly one topic.
    # Both ways it can break take what IS valid rather than discarding the response — the same
    # rule as entity-type naming, and for the same reason. Losing one need's placement beats
    # losing the structure of the whole cluster, which has already been paid for.
    drafts: list[TopicDraft] = []
    claimed: set[int] = set()
    for raw_topic in cast(list[Any], (data or {}).get("topics") or []):
        if not isinstance(raw_topic, dict):
            continue
        topic = cast(dict[str, Any], raw_topic)
        topic_name = str(topic.get("topic_name") or "").strip()
        if not topic_name:
            continue

        aspects: list[AspectDraft] = []
        for raw_aspect in cast(list[Any], topic.get("aspects") or []):
            if not isinstance(raw_aspect, dict):
                continue
            aspect = cast(dict[str, Any], raw_aspect)
            aspect_name = str(aspect.get("aspect_name") or "").strip()
            indices = json_completion.member_indices(aspect, len(members))
            if not aspect_name or not indices:
                continue
            # A need claimed twice would be written under two aspects and double-count the
            # fan-out, so the FIRST claim wins and later ones are dropped — not the aspect, and
            # not the response.
            fresh = [i for i in indices if i not in claimed]
            if len(fresh) != len(indices):
                log.warning(
                    "consolidate: %r claimed %d already-assigned need(s); keeping the first "
                    "assignment",
                    aspect_name,
                    len(indices) - len(fresh),
                )
            if not fresh:
                continue
            claimed.update(fresh)
            aspects.append(
                AspectDraft(
                    name=aspect_name,
                    description=str(aspect.get("aspect_description") or "").strip(),
                    members=[members[i] for i in fresh],
                )
            )

        if aspects:
            drafts.append(
                TopicDraft(
                    name=topic_name,
                    description=str(topic.get("topic_description") or "").strip(),
                    aspects=aspects,
                )
            )

    missing = len(members) - len(claimed)
    if missing:
        log.warning(
            "consolidate: %d of %d need(s) in a cluster were left unplaced and are absent from "
            "the map",
            missing,
            len(members),
        )
    return drafts


def _artifact(
    drafts: list[TopicDraft],
    *,
    fingerprint: str,
    entity_type_taxonomy_id: int | None,
    model: str | None,
    stats: dict[str, Any],
) -> dict[str, Any]:
    """The drafts in the shape ``need_map.record`` takes.

    Aspect ``key`` is scoped to its topic, because an aspect belongs to one topic — two subjects
    can each have an "implementation status" and they are different facets, not one shared row.
    """
    return {
        "corpus_fingerprint": fingerprint,
        "entity_type_taxonomy_id": entity_type_taxonomy_id,
        "provenance": {
            # "" when nothing is configured — the client resolved its own default, and we
            # cannot name what that was. Informational here; nothing compares it.
            "model": model or "",
            "cluster_similarity": topics.CLUSTER_SIMILARITY,
            "embed_model": embeddings.model_name(),
        },
        "stats": stats,
        "topics": [
            {
                "name": topic.name,
                "description": topic.description,
                "aspects": [
                    {
                        "key": f"{index}:{aspect.name}",
                        "name": aspect.name,
                        "description": aspect.description,
                        "pages": [
                            {"doc_id": ref.doc_id, "need_name": str(ref.need.get("need_name") or "")}
                            for ref in aspect.members
                        ],
                    }
                    for aspect in topic.aspects
                ],
            }
            for index, topic in enumerate(drafts)
        ],
    }


def consolidate(
    clusters: list[topics.Cluster], *, model: str | None = None, workers: int | None = None
) -> list[TopicDraft]:
    """Name every cluster, in parallel. Clusters the model could not structure are simply absent.

    Ordered by the cluster order it was given — which ``cluster_needs`` already made deterministic
    — so the same corpus produces the same map twice, ids aside.
    """
    if not clusters:
        return []
    count = workers or DEFAULT_WORKERS

    def one(cluster: topics.Cluster) -> list[TopicDraft]:
        try:
            return name_cluster(cluster, model=model)
        except Exception:
            log.warning(
                "consolidate: naming failed for a cluster of %d need(s)",
                len(cluster.members),
                exc_info=True,
            )
            return []

    with ThreadPoolExecutor(max_workers=count) as pool:
        results = list(pool.map(one, clusters))

    named = [draft for result in results for draft in result]
    failed = sum(1 for result in results if not result)
    if failed:
        log.warning(
            "consolidate: %d of %d cluster(s) produced no topics and are absent from the map",
            failed,
            len(clusters),
        )
    return named


def run_consolidation(
    *, model: str | None = None, triggered_by: str | None = None, workers: int | None = None
) -> int | None:
    """Derive a need map from the stored needs and record it. Returns the map id, or None.

    None on every outcome that is not a map: no needs, embeddings unavailable, or nothing named.
    Each would otherwise be recorded as a map saying the corpus shares nothing — which
    deactivates a good one and is indistinguishable from a real finding.
    """
    # Resolved ONCE per derivation, and to a NAME: provenance records which model produced these
    # names, and a switch landing mid-run must not leave one map labelled two ways. Same rule as
    # needs extraction, whose settings are runtime-configurable rather than static config.
    llm = get_llm_settings()
    # ``or None`` at the end, not "": an unconfigured deployment must let the client resolve its
    # own default, the way entity-type derivation does. Passing "" would name a model that does
    # not exist.
    model = model or llm.ingest_selector_model or llm.model or None
    rows = page_needs.load_all()
    refs = topics.load_needs()
    if not refs:
        log.info("consolidate: no stored needs; nothing to consolidate")
        return None

    clusters = topics.cluster_needs(refs)
    if clusters is None:
        log.warning("consolidate: embeddings unavailable; refusing to record a map")
        return None

    drafts = consolidate(clusters, model=model, workers=workers)
    if not drafts:
        log.warning("consolidate: no cluster produced a topic; refusing to record an empty map")
        return None

    n_aspects = sum(len(topic.aspects) for topic in drafts)
    spanning = [
        aspect
        for topic in drafts
        for aspect in topic.aspects
        if len({ref.doc_id for ref in aspect.members}) > 1
    ]
    stats = {
        "n_needs": len(refs),
        "n_clusters": len(clusters),
        "n_topics": len(drafts),
        "n_aspects": n_aspects,
        "n_aspects_spanning_pages": len(spanning),
        "widest_aspect_pages": max(
            (len({ref.doc_id for ref in aspect.members}) for aspect in spanning), default=1
        ),
    }
    log.info(
        "consolidate: %d need(s) -> %d cluster(s) -> %d topic(s), %d aspect(s); %d span >1 page, "
        "widest reaches %d",
        len(refs),
        len(clusters),
        len(drafts),
        n_aspects,
        len(spanning),
        stats["widest_aspect_pages"],
    )

    # Fingerprinted over the pages whose needs were CLUSTERED, not every stored page: a page
    # excluded by policy is not part of what this map was derived from, so re-enabling it must
    # read as a change.
    clustered = {ref.doc_id for ref in refs}
    fingerprint = need_map.corpus_fingerprint(
        [(row.doc_id, row.content_sha256) for row in rows if row.doc_id in clustered]
    )
    return need_map.record(
        _artifact(
            drafts,
            fingerprint=fingerprint,
            entity_type_taxonomy_id=entity_types.active_entity_type_taxonomy_id(),
            model=model,
            stats=stats,
        ),
        triggered_by=triggered_by,
    )
