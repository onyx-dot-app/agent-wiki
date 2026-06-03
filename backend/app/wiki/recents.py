"""Per-user recently-opened wiki docs — repo over ``RecentDocView``.

Tracks the docs a user actually opened (newest first), powering the
sidebar "Recents" list. Deliberately *not* derived from commit times:
agent/trigger/connector updates to docs the user never visited must not
reshuffle their recents.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.db.models import RecentDocView
from app.db.session import session

log = logging.getLogger(__name__)

# Rows kept per user. Larger than the sidebar shows so the list survives
# a few docs being deleted or un-shared without going short.
RECENTS_CAP = 50


def _now() -> str:
    # Microsecond precision (vs. the schema's second-precision default) so
    # docs opened in quick succession keep a deterministic order. Still
    # lexicographically comparable with second-precision values.
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")


def record_view(user_id: str, path: str) -> None:
    """Upsert a view for ``path`` and prune the user's rows beyond the cap."""
    with session() as s:
        stmt = pg_insert(RecentDocView).values(
            user_id=user_id, path=path, viewed_at=_now()
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[RecentDocView.user_id, RecentDocView.path],
            set_={"viewed_at": stmt.excluded.viewed_at},
        )
        s.execute(stmt)
        stale = s.scalars(
            select(RecentDocView.path)
            .where(RecentDocView.user_id == user_id)
            .order_by(RecentDocView.viewed_at.desc())
            .offset(RECENTS_CAP)
        ).all()
        if stale:
            s.execute(
                delete(RecentDocView).where(
                    RecentDocView.user_id == user_id,
                    RecentDocView.path.in_(stale),
                )
            )


def list_paths(user_id: str, limit: int = 20) -> list[str]:
    """The user's recently-opened doc paths, newest first."""
    with session() as s:
        return list(
            s.scalars(
                select(RecentDocView.path)
                .where(RecentDocView.user_id == user_id)
                .order_by(RecentDocView.viewed_at.desc())
                .limit(limit)
            ).all()
        )
