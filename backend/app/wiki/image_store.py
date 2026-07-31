"""Postgres-backed image blob storage seam."""

from __future__ import annotations

import hashlib
import uuid

from pydantic import BaseModel
from sqlalchemy import delete as sqla_delete
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, defer

from app.db.models import CoeditSession, Image, WikiDocId
from app.wiki.coedit import SessionStatus
from app.db.session import execute_dml, session


class StoredImage(BaseModel):
    id: str
    sha256: str
    content_type: str
    size_bytes: int
    anchor_doc_id: str
    uploaded_by: str | None
    created_at: str


class StoredImageData(StoredImage):
    data: bytes


class ImageSweepRow(BaseModel):
    id: str
    created_at: str
    unreferenced_since: str | None
    anchor_doc_id: str


def serving_url(image_id: str) -> str:
    """The app URL pages embed for an image."""
    return f"/api/wiki/images/{image_id}"


def sniff_image_type(data: bytes) -> str | None:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"GIF87a") or data.startswith(b"GIF89a"):
        return "image/gif"
    if len(data) >= 12 and data[0:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def _to_stored_image(row: Image) -> StoredImage:
    return StoredImage(
        id=row.id,
        sha256=row.sha256,
        content_type=row.content_type,
        size_bytes=row.size_bytes,
        anchor_doc_id=row.anchor_doc_id,
        uploaded_by=row.uploaded_by,
        created_at=row.created_at,
    )


def _to_stored_image_data(row: Image) -> StoredImageData:
    return StoredImageData(
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
    image_id = uuid.uuid4().hex[:16]
    with session() as s:
        s.add(
            Image(
                id=image_id,
                sha256=hashlib.sha256(data).hexdigest(),
                content_type=content_type,
                size_bytes=len(data),
                data=data,
                anchor_doc_id=anchor_doc_id,
                uploaded_by=uploaded_by,
            )
        )
    return image_id


def stat(image_id: str) -> StoredImage | None:
    with session() as s:
        row = s.scalar(
            select(Image).options(defer(Image.data)).where(Image.id == image_id)
        )
        return _to_stored_image(row) if row is not None else None


def get(image_id: str) -> StoredImageData | None:
    with session() as s:
        row = s.get(Image, image_id)
        return _to_stored_image_data(row) if row is not None else None


def list_for_sweep() -> list[ImageSweepRow]:
    with session() as s:
        rows = s.execute(
            select(
                Image.id,
                Image.created_at,
                Image.unreferenced_since,
                Image.anchor_doc_id,
            )
        ).all()
        return [
            ImageSweepRow(
                id=image_id,
                created_at=created_at,
                unreferenced_since=unreferenced_since,
                anchor_doc_id=anchor_doc_id,
            )
            for image_id, created_at, unreferenced_since, anchor_doc_id in rows
        ]


def set_unreferenced_since(image_id: str, value: str | None) -> None:
    with session() as s:
        s.execute(
            update(Image)
            .where(Image.id == image_id)
            .values(unreferenced_since=value)
            .execution_options(synchronize_session=False)
        )


def totals() -> tuple[int, int]:
    with session() as s:
        count, total_bytes = s.execute(
            select(func.count(), func.coalesce(func.sum(Image.size_bytes), 0))
        ).one()
        return int(count), int(total_bytes)


def delete_if_anchor_idle(image_id: str) -> bool:
    """Delete the image unless its anchor page has a live co-edit session.

    A Yjs buffer holds no server-readable text, so an open session is the only
    signal a draft may reference the image. At READ COMMITTED each statement
    takes its own snapshot, so the guarantee is only that no session visible at
    check time is lost. Returns True only when a row was deleted.
    """
    with session() as s:
        being_edited = s.scalar(
            select(func.count())
            .select_from(Image)
            .join(WikiDocId, WikiDocId.id == Image.anchor_doc_id)
            .join(CoeditSession, CoeditSession.path == WikiDocId.path)
            .where(
                Image.id == image_id,
                WikiDocId.deleted_at.is_(None),
                CoeditSession.status == SessionStatus.ACTIVE.value,
            )
        )
        if being_edited:
            return False
        return _delete_row(s, image_id)


def _delete_row(s: Session, image_id: str) -> bool:
    # Statement delete so the blob column is never fetched just to drop the row.
    stmt = (
        sqla_delete(Image)
        .where(Image.id == image_id)
        .execution_options(synchronize_session=False)
    )
    return execute_dml(s, stmt) > 0


def delete(image_id: str) -> bool:
    with session() as s:
        return _delete_row(s, image_id)
