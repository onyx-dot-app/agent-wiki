"""Read and write the derived topic layer.

    topic   the SUBJECT ("Wiki Auto Management")
    aspect  a FACET ("implementation status")
    pages   the documents holding that facet — the fan-out

An aspect can be a facet of more than one topic, so the link is an association table rather than
nesting. That, and a real foreign key from a page reference to ``wiki_doc_ids``, are why this is
five tables and not one JSONB document.

A derivation is written whole: ``record()`` inserts a run and everything under it in one
transaction, then flips ``active``. Nothing is updated in place, because clustering is global —
one need changing can move cluster membership anywhere, so there is no such thing as revising one
topic. Superseded runs stay readable; ids are stable only WITHIN a run, so a consumer that records
a decision against an aspect must keep the run id with it.

Reads return detached records, not ORM rows, per the repo convention: the rest of the app should
not depend on SQLAlchemy, and a read must not become a lazy load against a closed session.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from typing import Any, NamedTuple, cast

from sqlalchemy import delete as sa_delete, select, update

from app.db.models import Aspect, AspectPage, Topic, TopicAspect, TopicMapRun
from app.db.session import advisory_xact_lock, session

log = logging.getLogger(__name__)

# Serializes record(). The whole DB shares one 64-bit advisory keyspace (see triggers/repo.py's
# _REBUILD_ADVISORY_LOCK), so this is a distinct constant. Spells "tmap".
_RECORD_ADVISORY_LOCK = 0x746D6170


class PageRef(NamedTuple):
    """A page holding an aspect. ``need_name`` is provenance; the live need lives in page_needs."""

    doc_id: str
    need_name: str
    entity: str
    # How THIS page maintains the facet — authoritative for writing to it.
    aspect_kind: str
    detail_level: str
    focus: str


class AspectRecord(NamedTuple):
    """One facet, with the pages holding it. ``pages`` of length > 1 is the fan-out.

    ``aspect_id``, not ``id``: the same value is called ``aspect_id`` in every table that
    references it, and one name for one thing is worth more than brevity here.
    """

    aspect_id: int
    name: str
    description: str
    pages: list[PageRef]

    @property
    def spans_pages(self) -> bool:
        return len({p.doc_id for p in self.pages}) > 1

    # The three below summarize the pages for triage — filtering to timeline aspects, or deciding
    # whether an aspect is worth loading. Computed rather than stored: both loaders fetch an
    # aspect's full page list before building this record, so a column would buy nothing except
    # the chance to disagree with the rows it summarizes. The authoritative value for writing to a
    # page is always that page's own, on ``PageRef``.

    @property
    def dominant_kind(self) -> str:
        """The most common ``aspect_kind`` among the pages; "" when there are none.

        A mode is meaningful here and only here: the vocabulary is a closed four-value set, so
        pages genuinely land on the same value. Ties break by name, so the answer does not depend
        on row order — the point of a summary is that it is the same summary twice.
        """
        counts: dict[str, int] = {}
        for page in self.pages:
            counts[page.aspect_kind] = counts.get(page.aspect_kind, 0) + 1
        return min(counts, key=lambda k: (-counts[k], k)) if counts else ""

    @property
    def shared_detail_level(self) -> str:
        """The granularity, when every page states the same one; "" when they differ.

        Free text, so there is no mode to take — two pages can describe identical granularity in
        words that do not match, and picking one would present an arbitrary page's phrasing as the
        aspect's. Empty says what is true: read the page rows.
        """
        levels = {p.detail_level for p in self.pages}
        return levels.pop() if len(levels) == 1 else ""

    @property
    def shared_focus(self) -> str:
        """The entity-set focus, when unanimous; "" when the pages disagree.

        Unanimity rather than a mode, because this one gates admission: ``generic`` here while one
        page is ``specific`` would summarize the aspect as open when a page holding it is closed.
        Disagreement reads as "" — never as "generic" — matching the fail-safe on the need itself.
        """
        focuses = {p.focus for p in self.pages}
        return focuses.pop() if len(focuses) == 1 else ""


class TopicRecord(NamedTuple):
    topic_id: int
    name: str
    description: str
    aspects: list[AspectRecord]


class TopicMap(NamedTuple):
    """One run's whole topic layer."""

    run_id: int
    corpus_fingerprint: str
    entity_type_taxonomy_id: int | None
    provenance: dict[str, Any]
    stats: dict[str, Any]
    created_at: str
    topics: list[TopicRecord]


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def corpus_fingerprint(pages: list[tuple[str, str]]) -> str:
    """Fingerprint the needs a run was derived from, as ``(doc_id, content_sha256)`` pairs.

    Answers "have the needs moved since this was derived?", which decides whether re-deriving is
    worth an LLM pass — and matters because the naming step is a sampled call, so re-deriving on
    unchanged needs would churn topic names for nothing. Sorted, so the answer does not depend on
    read order.
    """
    digest = hashlib.sha256()
    for doc_id, sha in sorted(pages):
        digest.update(doc_id.encode("utf-8"))
        digest.update(b"\x1f")
        digest.update(sha.encode("utf-8"))
        digest.update(b"\x1e")
    return digest.hexdigest()


def _load(db: Any, run: TopicMapRun) -> TopicMap:
    """Assemble one run into detached records. Four queries, not one per topic."""
    topics = list(db.scalars(select(Topic).where(Topic.run_id == run.id).order_by(Topic.id)))
    aspects = list(db.scalars(select(Aspect).where(Aspect.run_id == run.id).order_by(Aspect.id)))
    aspect_ids = [a.id for a in aspects]

    pages_by_aspect: dict[int, list[PageRef]] = {}
    if aspect_ids:
        for row in db.scalars(
            select(AspectPage)
            .where(AspectPage.aspect_id.in_(aspect_ids))
            .order_by(AspectPage.aspect_id, AspectPage.doc_id, AspectPage.entity)
        ):
            pages_by_aspect.setdefault(row.aspect_id, []).append(
                PageRef(
                    doc_id=row.doc_id,
                    need_name=row.need_name,
                    entity=row.entity,
                    aspect_kind=row.aspect_kind,
                    detail_level=row.detail_level,
                    focus=row.focus,
                )
            )

    records = {
        a.id: AspectRecord(
            aspect_id=a.id,
            name=a.name,
            description=a.description,
            pages=pages_by_aspect.get(a.id, []),
        )
        for a in aspects
    }

    links: dict[int, list[int]] = {}
    if topics:
        for link in db.scalars(
            select(TopicAspect).where(TopicAspect.topic_id.in_([t.id for t in topics]))
        ):
            links.setdefault(link.topic_id, []).append(link.aspect_id)

    return TopicMap(
        run_id=run.id,
        corpus_fingerprint=run.corpus_fingerprint,
        entity_type_taxonomy_id=run.entity_type_taxonomy_id,
        provenance=dict(run.provenance or {}),
        stats=dict(run.stats or {}),
        created_at=run.created_at,
        topics=[
            TopicRecord(
                topic_id=t.id,
                name=t.name,
                description=t.description,
                aspects=[records[i] for i in links.get(t.id, []) if i in records],
            )
            for t in topics
        ],
    )


def active() -> TopicMap | None:
    """The run in force, or None when nothing has been derived.

    None is a normal state: a deployment that has never derived has no topic layer, and callers
    fall back to per-page needs rather than failing.
    """
    with session() as db:
        run = db.scalars(select(TopicMapRun).where(TopicMapRun.active)).one_or_none()
        return _load(db, run) if run else None


def get(run_id: int) -> TopicMap | None:
    """A specific run — how a consumer resolves the aspect it recorded a decision against, since
    ids are only stable within a run."""
    with session() as db:
        run = db.get(TopicMapRun, run_id)
        return _load(db, run) if run else None


def aspects_for_page(doc_id: str) -> list[AspectRecord]:
    """Which aspects the active run says this page holds — the reverse lookup a reconciler needs.

    An indexed query rather than a scan, which is the practical reason this is tables and not a
    document: at ten thousand pages the equivalent blob is ~8.5 MB, fine to load once and cache
    but not to read per incoming document.
    """
    with session() as db:
        run = db.scalars(select(TopicMapRun).where(TopicMapRun.active)).one_or_none()
        if run is None:
            return []
        rows = db.execute(
            select(Aspect, AspectPage)
            .join(AspectPage, AspectPage.aspect_id == Aspect.id)
            .where(Aspect.run_id == run.id, AspectPage.doc_id == doc_id)
            .order_by(Aspect.id)
        ).all()
        found = [a for a, _ in rows]
        if not found:
            return []
        pages_by_aspect: dict[int, list[PageRef]] = {}
        for page in db.scalars(
            select(AspectPage).where(AspectPage.aspect_id.in_([a.id for a in found]))
        ):
            pages_by_aspect.setdefault(page.aspect_id, []).append(
                PageRef(
                    doc_id=page.doc_id,
                    need_name=page.need_name,
                    entity=page.entity,
                    aspect_kind=page.aspect_kind,
                    detail_level=page.detail_level,
                    focus=page.focus,
                )
            )
        return [
            AspectRecord(
                aspect_id=a.id,
                name=a.name,
                description=a.description,
                pages=pages_by_aspect.get(a.id, []),
            )
            for a in found
        ]


def history(limit: int = 20) -> list[TopicMapRun]:
    """Run headers, newest first. For seeing what a re-derivation changed without loading each."""
    with session() as db:
        return list(db.scalars(select(TopicMapRun).order_by(TopicMapRun.id.desc()).limit(limit)))


def record(artifact: dict[str, Any], *, triggered_by: str | None = None) -> int:
    """Store a derived topic map and make it active. Returns the run id.

    The artifact's ``topics`` carry their aspects inline; an aspect appearing under more than one
    topic is written ONCE and linked twice, keyed by the producer's ``key`` (its identity within
    this run). That is the shape nesting could not express.

    Whole-run insert under an advisory lock. The partial unique index alone is not enough to make
    concurrent recorders safe: two workers would each deactivate the row their statement could
    see, both insert active rows, and the second would roll back — losing a derivation that had
    already paid for its LLM calls. The lock makes the second WAIT and then succeed.
    """
    topics = cast(list[dict[str, Any]], artifact.get("topics") or [])
    if not topics:
        raise ValueError("refusing to record a topic map with no topics")

    with session() as db:
        advisory_xact_lock(db, _RECORD_ADVISORY_LOCK)
        db.execute(update(TopicMapRun).where(TopicMapRun.active).values(active=False))
        run = TopicMapRun(
            active=True,
            corpus_fingerprint=str(artifact.get("corpus_fingerprint") or ""),
            entity_type_taxonomy_id=artifact.get("entity_type_taxonomy_id"),
            provenance=artifact.get("provenance") or {},
            stats=artifact.get("stats") or {},
            triggered_by=triggered_by,
            created_at=_now(),
        )
        db.add(run)
        db.flush()

        # An aspect shared by two topics is one row with two links. Keyed by the producer's own
        # identity for it; falling back to the name means a producer that omits keys still gets
        # sane de-duplication rather than silent duplicates.
        aspect_ids: dict[str, int] = {}
        n_aspects = n_pages = n_links = 0
        for topic in topics:
            topic_row = Topic(
                run_id=run.id,
                name=str(topic.get("name") or ""),
                description=str(topic.get("description") or ""),
            )
            db.add(topic_row)
            db.flush()

            for aspect in cast(list[dict[str, Any]], topic.get("aspects") or []):
                key = str(aspect.get("key") or aspect.get("name") or "")
                aspect_id = aspect_ids.get(key)
                if aspect_id is None:
                    # aspect_kind / detail_level / focus are read from the artifact but not stored
                    # on the aspect: they serve as the per-page default below, so a producer can
                    # state a facet's shape once instead of repeating it on every page.
                    aspect_row = Aspect(
                        run_id=run.id,
                        name=str(aspect.get("name") or ""),
                        description=str(aspect.get("description") or ""),
                    )
                    db.add(aspect_row)
                    db.flush()
                    aspect_id = aspect_row.id
                    aspect_ids[key] = aspect_id
                    n_aspects += 1
                    # Deduped here as well as constrained in the schema. The key alone would
                    # abort the whole run on a repeated page — losing a derivation that already
                    # paid for its LLM calls — so a producer listing a page twice loses the
                    # duplicate, not the run. The constraint stays as the backstop.
                    seen: set[tuple[str, str]] = set()
                    for page in cast(list[dict[str, Any]], aspect.get("pages") or []):
                        doc_id = str(page.get("doc_id") or "")
                        entity = str(page.get("entity") or "")
                        if (doc_id, entity) in seen:
                            log.debug(
                                "topic_map: dropping repeated page %s (entity %r) on aspect %r",
                                doc_id,
                                entity,
                                aspect.get("name"),
                            )
                            continue
                        seen.add((doc_id, entity))
                        db.add(
                            AspectPage(
                                aspect_id=aspect_id,
                                doc_id=doc_id,
                                need_name=str(page.get("need_name") or ""),
                                entity=entity,
                                aspect_kind=str(page.get("aspect_kind") or aspect.get("aspect_kind") or ""),
                                detail_level=str(page.get("detail_level") or aspect.get("detail_level") or ""),
                                focus=str(page.get("focus") or aspect.get("focus") or ""),
                            )
                        )
                        n_pages += 1
                db.add(TopicAspect(topic_id=topic_row.id, aspect_id=aspect_id))
                n_links += 1

        db.commit()
        run_id = run.id

    log.info(
        "topic_map: recorded run %d — %d topic(s), %d aspect(s), %d link(s), %d page ref(s)",
        run_id,
        len(topics),
        n_aspects,
        n_links,
        n_pages,
    )
    return run_id


def prune(keep: int = 5) -> int:
    """Drop all but the newest ``keep`` runs. Returns how many went.

    Everything cascades from the run, so this is one delete. Old runs are kept at all so a
    re-derivation's effect can be compared — the naming step is sampled, and topic names have been
    observed to churn between runs over an unchanged corpus.
    """
    with session() as db:
        ids = list(db.scalars(select(TopicMapRun.id).order_by(TopicMapRun.id.desc()).offset(keep)))
        if not ids:
            return 0
        db.execute(sa_delete(TopicMapRun).where(TopicMapRun.id.in_(ids)))
        return len(ids)
