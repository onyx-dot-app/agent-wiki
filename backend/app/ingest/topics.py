"""Group per-page needs into the facets that SPAN pages.

Need extraction is per page, so it cannot see that eleven pages all track implementation status.
This is the step that can: embed every need, cluster the embeddings, and the clusters that reach
more than one page are the shared facets. That reach is the whole premise — a fact belonging to a
shared facet can be reconciled once and applied to every page holding it, instead of each page
independently deciding what to do with the same document.

Measured on 238 needs from a 149-page production wiki: 42% land in a cluster spanning more than
one page, the widest reaching 24. So the fan-out is real, and so is the half that is genuinely
page-local — a clustering that claimed everything was shared would be wrong.

This module only groups. Naming a cluster's topic and aspects is an LLM step and lives apart, so
the grouping stays deterministic and inspectable on its own.
"""

from __future__ import annotations

import logging
from typing import Any, NamedTuple

from app.db import page_needs
from app.ingest.clustering import leader_cluster, normalize
from app.llm import embeddings
from app.wiki import update_policy

log = logging.getLogger(__name__)

# Cosine floor for "the same facet". Tuned for leader clustering and for the key below — both
# together, since neither transfers alone.
#
# Measured against two labels held OUT of the key (a need's kind, and its primary entity's type):
# real facet clusters should agree with signals they never saw. At ~150 clusters over 238 needs,
# name+description at 0.60 scores 71% kind / 73% entity-type agreement, against 68% / 65% for a
# name-only key at its own matched granularity.
#
# Comparing keys at a FIXED threshold is the trap: adding description raises every pairwise
# similarity, so the same number buys coarser clusters and looks worse for reasons that have
# nothing to do with the key's quality. Compare at matched cluster counts instead.
CLUSTER_SIMILARITY = 0.60


class NeedRef(NamedTuple):
    """One need, with the page it came from. ``doc_id`` rather than a path because a page can be
    renamed between clustering and anything acting on the result."""

    doc_id: str
    path: str
    need: dict[str, Any]


class Cluster(NamedTuple):
    """Needs believed to be the same facet.

    ``pages`` is the fan-out: the distinct documents this facet reaches. A single-page cluster is
    a normal outcome, not a failure — most of what a page tracks is its own.
    """

    members: list[NeedRef]

    @property
    def pages(self) -> set[str]:
        return {m.doc_id for m in self.members}

    @property
    def spans_pages(self) -> bool:
        return len(self.pages) > 1


def embed_key(need: dict[str, Any]) -> str:
    """The text that decides which cluster a need joins.

    ``need_name`` and ``description`` — what the page tracks and how it frames it. Everything
    else is left out deliberately:

    ``current_content``  the STATE, which churns on every page edit. Including it would move a
                         need between clusters when its content changed rather than when what it
                         tracks changed, and a need exists precisely because the spec outlives
                         the content.
    ``need_kind``        a closed four-value vocabulary. Appending it pulled 113 "reference"
                         needs together and produced a 70-page cluster: grouping by kind rather
                         than by subject, which is the opposite of the point. (An open-vocabulary
                         type does discriminate, which is why the upstream eval could use one.)
    ``entities``         the other axis. An entity is the ROW and a facet is the COLUMN, so
                         embedding entities would cluster by subject — "everything about Scania"
                         — instead of across subjects — "deal status, for every customer" — and
                         collapse exactly the fan-out this step exists to find.
    ``detail_level`` / ``update_instruction`` / ``focus``
                         how the need is maintained, not what it is about.
    """
    description = (need.get("description") or "").strip()
    name = (need.get("need_name") or "").strip()
    return f"{name}. {description}" if description else name


def cluster_needs(
    needs: list[NeedRef], *, similarity: float = CLUSTER_SIMILARITY
) -> list[Cluster] | None:
    """Group needs into facets. ``None`` when embeddings are unavailable.

    ``None`` rather than one-cluster-per-need: with no embeddings every need looks unrelated to
    every other, which is indistinguishable from a corpus that genuinely shares nothing. A caller
    must not record that as a finding.

    Needs are seeded in page order so the result is deterministic — the same corpus clusters the
    same way twice, which a downstream naming step depends on to be reproducible.
    """
    if not needs:
        return []

    vectors = embeddings.embed_texts([embed_key(m.need) for m in needs])
    if vectors is None:
        log.warning("topics: embeddings unavailable; cannot cluster %d need(s)", len(needs))
        return None

    unit = [normalize(v) for v in vectors]
    groups = leader_cluster(unit, list(range(len(unit))), similarity)
    clusters = [Cluster(members=[needs[i] for i in group]) for group in groups]
    clusters.sort(key=lambda c: (-len(c.pages), -len(c.members)))

    spanning = [c for c in clusters if c.spans_pages]
    reached = sum(len(c.members) for c in spanning)
    log.info(
        "topics: %d need(s) -> %d cluster(s); %d span >1 page, holding %d need(s) (%.0f%%), "
        "widest reaches %d page(s)",
        len(needs),
        len(clusters),
        len(spanning),
        reached,
        100 * reached / len(needs),
        len(clusters[0].pages) if clusters else 0,
    )
    return clusters


def load_needs() -> list[NeedRef]:
    """Every stored need that belongs to a page the ingestion pipeline may auto-update.

    Extraction already skips disabled pages and prunes ones turned off since, so this filter is
    for the window between the two: a page disabled after the last extraction still has needs
    stored, and clustering must not offer it as somewhere a fact could be reconciled to. Checked
    here rather than trusted from extraction so the result does not depend on when that last ran.

    Deleted and trashed pages are already excluded by ``load_all``.
    """
    rows = page_needs.load_all()
    disabled = update_policy.disabled_paths([row.path for row in rows])
    if disabled:
        log.info("topics: excluding %d page(s) with ingestion auto-update disabled", len(disabled))
    return [
        NeedRef(doc_id=row.doc_id, path=row.path, need=need)
        for row in rows
        if row.path not in disabled
        for need in row.needs
    ]
