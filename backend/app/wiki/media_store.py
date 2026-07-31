"""Postgres-backed media blob storage seam (images, video, gif)."""

from __future__ import annotations

import hashlib
import uuid

from pydantic import BaseModel, ConfigDict
from sqlalchemy import delete as sqla_delete
from sqlalchemy import func, select, update
from sqlalchemy.orm import defer

from app.db.models import Media
from app.db.session import execute_dml, session


class StoredMedia(BaseModel):
    id: str
    sha256: str
    content_type: str
    size_bytes: int
    anchor_doc_id: str
    uploaded_by: str | None
    created_at: str


class StoredMediaData(StoredMedia):
    data: bytes


class MediaSweepRow(BaseModel):
    id: str
    created_at: str
    unreferenced_since: str | None
    anchor_doc_id: str


def serving_url(media_id: str) -> str:
    """The app URL pages embed for a stored media object."""
    return f"/api/wiki/media/{media_id}"


def is_same_origin_src(src: str) -> bool:
    """Whether ``src`` loads from this origin.

    A scheme (``https:``, ``data:``) or a protocol-relative ``//`` prefix points
    somewhere this app does not serve, so every reader's browser would announce
    itself to that host. Relative paths and this app's own routes resolve here
    and are left alone. Supporting external sources needs a proxy that re-serves
    them from this origin.
    """
    trimmed = src.strip()
    if not trimmed or trimmed.startswith("//"):
        return False
    return ":" not in trimmed.split("/", 1)[0]


class _Magic(BaseModel):
    """One file-signature rule: bytes expected at an offset, and what they mean."""

    model_config = ConfigDict(frozen=True)

    offset: int
    signature: bytes
    content_type: str
    # Some containers identify themselves in two places (RIFF....WEBP).
    also_at: int | None = None
    also: bytes | None = None


# Ordered, first match wins. Adding a format is a row, not a branch. Anything
# absent is rejected: the type here is what gets served back, so a format the
# browser would sniff differently must never be stored.
_SIGNATURES: tuple[_Magic, ...] = (
    _Magic(offset=0, signature=b"\x89PNG\r\n\x1a\n", content_type="image/png"),
    _Magic(offset=0, signature=b"\xff\xd8\xff", content_type="image/jpeg"),
    _Magic(offset=0, signature=b"GIF87a", content_type="image/gif"),
    _Magic(offset=0, signature=b"GIF89a", content_type="image/gif"),
    _Magic(
        offset=0,
        signature=b"RIFF",
        content_type="image/webp",
        also_at=8,
        also=b"WEBP",
    ),
)


def sniff_media_type(data: bytes) -> str | None:
    """The stored and served content type, decided by the bytes themselves.

    A declared type is never trusted, so a mislabelled upload cannot make the
    server hand back a type the browser then interprets differently.
    """
    for rule in _SIGNATURES:
        end = rule.offset + len(rule.signature)
        if data[rule.offset : end] != rule.signature:
            continue
        if rule.also is not None and rule.also_at is not None:
            also_end = rule.also_at + len(rule.also)
            if data[rule.also_at : also_end] != rule.also:
                continue
        return rule.content_type
    return None


def _to_stored_media(row: Media) -> StoredMedia:
    return StoredMedia(
        id=row.id,
        sha256=row.sha256,
        content_type=row.content_type,
        size_bytes=row.size_bytes,
        anchor_doc_id=row.anchor_doc_id,
        uploaded_by=row.uploaded_by,
        created_at=row.created_at,
    )


def _to_stored_media_data(row: Media) -> StoredMediaData:
    return StoredMediaData(
        id=row.id,
        sha256=row.sha256,
        content_type=row.content_type,
        size_bytes=row.size_bytes,
        anchor_doc_id=row.anchor_doc_id,
        uploaded_by=row.uploaded_by,
        created_at=row.created_at,
        data=row.data,
    )


def put(
    *, data: bytes, content_type: str, anchor_doc_id: str, uploaded_by: str | None
) -> str:
    media_id = uuid.uuid4().hex[:16]
    with session() as s:
        s.add(
            Media(
                id=media_id,
                sha256=hashlib.sha256(data).hexdigest(),
                content_type=content_type,
                size_bytes=len(data),
                data=data,
                anchor_doc_id=anchor_doc_id,
                uploaded_by=uploaded_by,
            )
        )
    return media_id


def stat(media_id: str) -> StoredMedia | None:
    with session() as s:
        row = s.scalar(
            select(Media).options(defer(Media.data)).where(Media.id == media_id)
        )
        return _to_stored_media(row) if row is not None else None


def get(media_id: str) -> StoredMediaData | None:
    with session() as s:
        row = s.get(Media, media_id)
        return _to_stored_media_data(row) if row is not None else None


def list_for_sweep() -> list[MediaSweepRow]:
    with session() as s:
        rows = s.execute(
            select(
                Media.id,
                Media.created_at,
                Media.unreferenced_since,
                Media.anchor_doc_id,
            )
        ).all()
        return [
            MediaSweepRow(
                id=media_id,
                created_at=created_at,
                unreferenced_since=unreferenced_since,
                anchor_doc_id=anchor_doc_id,
            )
            for media_id, created_at, unreferenced_since, anchor_doc_id in rows
        ]


def set_unreferenced_since(media_id: str, value: str | None) -> None:
    with session() as s:
        s.execute(
            update(Media)
            .where(Media.id == media_id)
            .values(unreferenced_since=value)
            .execution_options(synchronize_session=False)
        )


def totals() -> tuple[int, int]:
    with session() as s:
        count, total_bytes = s.execute(
            select(func.count(), func.coalesce(func.sum(Media.size_bytes), 0))
        ).one()
        return int(count), int(total_bytes)


def delete_if_still_flagged(media_id: str, flagged_at: str) -> bool:
    """Delete the row only while it carries the exact flag the caller saw.

    Compare-and-delete in one statement, so anything that cleared or refreshed
    ``unreferenced_since`` in the meantime wins and the row survives. Whether
    it is still cited is the caller's question, answered against the working
    tree and live drafts. Returns True only when a row was deleted.
    """
    with session() as s:
        stmt = (
            sqla_delete(Media)
            .where(Media.id == media_id, Media.unreferenced_since == flagged_at)
            .execution_options(synchronize_session=False)
        )
        return execute_dml(s, stmt) > 0


def delete(media_id: str) -> bool:
    # Statement delete so the blob column is never fetched just to drop the row.
    with session() as s:
        stmt = (
            sqla_delete(Media)
            .where(Media.id == media_id)
            .execution_options(synchronize_session=False)
        )
        return execute_dml(s, stmt) > 0
