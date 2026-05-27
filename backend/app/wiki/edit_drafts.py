"""In-progress human edit drafts — one row per (page, user).

Auto-saved by the frontend while editing; deleted on successful commit.
Allows the server to detect stale drafts when a user reopens a page.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import delete, text
from sqlalchemy.dialects.postgresql import insert

from app.db.models import WikiEditDraft
from app.db.session import session


def _to_dict(row: WikiEditDraft) -> dict[str, Any]:
    return {
        "path": row.path,
        "user_id": row.user_id,
        "base_sha": row.base_sha,
        "content": row.content,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def get(path: str, user_id: str) -> dict[str, Any] | None:
    with session() as s:
        row = (
            s.query(WikiEditDraft)
            .filter_by(path=path, user_id=user_id)
            .first()
        )
        return _to_dict(row) if row else None


def upsert(*, path: str, user_id: str, base_sha: str, content: str) -> None:
    now = text("(now() AT TIME ZONE 'UTC')::text")
    stmt = (
        insert(WikiEditDraft)
        .values(path=path, user_id=user_id, base_sha=base_sha, content=content)
        .on_conflict_do_update(
            constraint="wiki_edit_drafts_path_user_id_key",
            set_={"base_sha": base_sha, "content": content, "updated_at": now},
        )
    )
    with session() as s:
        s.execute(stmt)


def delete_draft(path: str, user_id: str) -> None:
    with session() as s:
        s.execute(
            delete(WikiEditDraft).where(
                WikiEditDraft.path == path, WikiEditDraft.user_id == user_id
            )
        )


def delete_for_path(path: str) -> None:
    """Remove all drafts for a page — call when the page is deleted."""
    with session() as s:
        s.execute(delete(WikiEditDraft).where(WikiEditDraft.path == path))
