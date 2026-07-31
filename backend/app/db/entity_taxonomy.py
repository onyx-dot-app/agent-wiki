"""Read and write derived entity-type taxonomies.

Append-only with one active row. The reason is that entity types are KEYS: anything that
records facts per entity records them under a type name, so a re-derivation that renames a
type would orphan every row pointing at the old one. Overwriting a single current value
makes that failure silent and unrecoverable; keeping a history makes a rename a visible
event with the superseded taxonomy still readable.

So ``record()`` inserts and promotes rather than updating, and the previous row stays. A
consumer that keys by type should store the taxonomy ``id`` it used alongside, and resolve
through ``get(id)`` rather than assuming the active one still means what it meant.
"""

from __future__ import annotations

import logging
from typing import Any, cast

from sqlalchemy import select, update

from app.db.models import EntityTaxonomy
from app.db.session import session

log = logging.getLogger(__name__)


def active() -> EntityTaxonomy | None:
    """The taxonomy in force, or None when nothing has been derived yet.

    None is a normal state, not an error — a deployment that has never run a derivation has
    no taxonomy, and callers fall back to a generic type list (see
    ``app.ingest.entity_types.load_taxonomy``).
    """
    with session() as db:
        return db.scalars(select(EntityTaxonomy).where(EntityTaxonomy.active)).one_or_none()


def get(taxonomy_id: int) -> EntityTaxonomy | None:
    """A specific taxonomy by id — how a consumer resolves the types it keyed facts under."""
    with session() as db:
        return db.get(EntityTaxonomy, taxonomy_id)


def history(limit: int = 20) -> list[EntityTaxonomy]:
    """Newest first. For seeing what a re-derivation changed."""
    with session() as db:
        return list(
            db.scalars(select(EntityTaxonomy).order_by(EntityTaxonomy.id.desc()).limit(limit))
        )


def record(artifact: dict[str, Any], *, triggered_by: str | None = None) -> int:
    """Store a derived taxonomy and make it active. Returns its id.

    Takes the artifact ``app.ingest.entity_types.derive`` produces, so the producer decides
    what is worth keeping rather than this layer imposing a shape.

    Deactivating the previous row and inserting the new one happen in one transaction: the
    partial unique index on ``active`` means a half-applied write would either leave no
    taxonomy in force or fail outright, and the second is much easier to notice.
    """
    types = cast(list[dict[str, Any]], artifact.get("entity_types") or [])
    if not types:
        raise ValueError("refusing to record a taxonomy with no types")

    with session() as db:
        db.execute(update(EntityTaxonomy).where(EntityTaxonomy.active).values(active=False))
        row = EntityTaxonomy(
            active=True,
            corpus_fingerprint=str(artifact.get("corpus_fingerprint") or ""),
            types=types,
            provenance=artifact.get("provenance") or {},
            stats=artifact.get("stats") or {},
            triggered_by=triggered_by,
        )
        db.add(row)
        db.commit()
        taxonomy_id = row.id

    log.info(
        "entity_taxonomy: recorded taxonomy %d (%d type(s), corpus %s)",
        taxonomy_id,
        len(types),
        str(artifact.get("corpus_fingerprint") or "")[:12],
    )
    return taxonomy_id
