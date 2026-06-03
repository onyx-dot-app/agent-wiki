"""Per-user starred wiki docs — repo over ``StarredDoc``.

Starred docs are pinned in their own sidebar section above Recents and
keep a user-chosen order: ``star`` appends at the end, ``reorder``
rewrites positions from a dragged full ordering.
"""

from __future__ import annotations

import logging

from sqlalchemy import delete, func, select, update

from app.db.models import StarredDoc
from app.db.session import session

log = logging.getLogger(__name__)

# Hard ceiling so a runaway client can't grow the pinned list unbounded.
STARRED_CAP = 100


def star(user_id: str, path: str) -> None:
    """Pin ``path`` at the end of the user's starred list. No-op if
    already starred (keeps its position) or if the cap is reached."""
    with session() as s:
        if s.get(StarredDoc, (user_id, path)) is not None:
            return
        count = (
            s.scalar(
                select(func.count())
                .select_from(StarredDoc)
                .where(StarredDoc.user_id == user_id)
            )
            or 0
        )
        if count >= STARRED_CAP:
            log.warning("starred cap reached user=%s path=%s", user_id, path)
            return
        next_order = (
            s.scalar(
                select(func.max(StarredDoc.sort_order)).where(
                    StarredDoc.user_id == user_id
                )
            )
            or 0
        ) + 1
        s.add(StarredDoc(user_id=user_id, path=path, sort_order=next_order))


def unstar(user_id: str, path: str) -> None:
    with session() as s:
        s.execute(
            delete(StarredDoc).where(
                StarredDoc.user_id == user_id, StarredDoc.path == path
            )
        )


def list_paths(user_id: str) -> list[str]:
    """The user's starred doc paths in their chosen order."""
    with session() as s:
        return list(
            s.scalars(
                select(StarredDoc.path)
                .where(StarredDoc.user_id == user_id)
                .order_by(StarredDoc.sort_order.asc())
            ).all()
        )


def reorder(user_id: str, paths: list[str]) -> None:
    """Persist a drag-reorder: positions follow the index in ``paths``.
    Paths not in the list (e.g. starred concurrently from another tab)
    keep rows but sink to the end in their previous relative order."""
    with session() as s:
        current = s.scalars(
            select(StarredDoc.path)
            .where(StarredDoc.user_id == user_id)
            .order_by(StarredDoc.sort_order.asc())
        ).all()
        listed = [p for p in paths if p in set(current)]
        trailing = [p for p in current if p not in set(listed)]
        for i, p in enumerate(listed + trailing):
            s.execute(
                update(StarredDoc)
                .where(StarredDoc.user_id == user_id, StarredDoc.path == p)
                .values(sort_order=i)
            )
