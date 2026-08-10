"""Aspect states — what each aspect's needs say right now, unified.

The read/write seam for ``aspect_states`` (see the model docstring for what a
row means and how it goes stale). Same boundary rules as ``page_needs``:
NamedTuple records out, no ORM objects, each function its own session.
"""

from __future__ import annotations

from typing import NamedTuple

from sqlalchemy import select

from app.db.models import Aspect, AspectState
from app.db.session import session
from sqlalchemy.dialects.postgresql import insert as pg_insert


class StoredAspectState(NamedTuple):
    """One aspect's stored state."""

    aspect_id: int
    state: str
    conflict: bool
    conflict_note: str
    model: str
    updated_at: str


def record(
    aspect_id: int,
    *,
    state: str,
    conflict: bool,
    conflict_note: str,
    model: str,
) -> None:
    """Upsert one aspect's state. Per aspect, so a generation pass that dies
    keeps everything it already produced — same shape as ``page_needs.store``."""
    with session() as s:
        stmt = pg_insert(AspectState).values(
            aspect_id=aspect_id,
            state=state,
            conflict=conflict,
            conflict_note=conflict_note,
            model=model,
        )
        s.execute(
            stmt.on_conflict_do_update(
                index_elements=[AspectState.aspect_id],
                set_={
                    "state": stmt.excluded.state,
                    "conflict": stmt.excluded.conflict,
                    "conflict_note": stmt.excluded.conflict_note,
                    "model": stmt.excluded.model,
                    "updated_at": stmt.excluded.updated_at,
                },
            )
        )


def get(aspect_id: int) -> StoredAspectState | None:
    with session() as s:
        row = s.get(AspectState, aspect_id)
        return _record(row) if row is not None else None


def load_for_map(need_map_id: int) -> list[StoredAspectState]:
    """Every stored state whose aspect belongs to ``need_map_id``."""
    with session() as s:
        rows = s.scalars(
            select(AspectState)
            .join(Aspect, Aspect.id == AspectState.aspect_id)
            .where(Aspect.need_map_id == need_map_id)
        ).all()
        return [_record(r) for r in rows]


def _record(row: AspectState) -> StoredAspectState:
    return StoredAspectState(
        aspect_id=row.aspect_id,
        state=row.state,
        conflict=row.conflict,
        conflict_note=row.conflict_note,
        model=row.model,
        updated_at=row.updated_at,
    )
