"""Wiki search — thin wrapper over the FTS index."""
from __future__ import annotations

from app.db import fts


def search(query: str, limit: int = 20) -> list[dict]:
    return fts.search(query, limit=limit)
