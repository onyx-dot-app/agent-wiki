"""Wiki search — thin wrapper over the FTS index."""
from __future__ import annotations

from app.db import fts
from app.db.fts import SearchHit


def search(
    query: str,
    limit: int = 20,
    *,
    user_id: str | None = None,
    is_admin: bool = False,
    apply_visibility: bool = True,
) -> list[SearchHit]:
    return fts.search(
        query,
        limit=limit,
        user_id=user_id,
        is_admin=is_admin,
        apply_visibility=apply_visibility,
    )
