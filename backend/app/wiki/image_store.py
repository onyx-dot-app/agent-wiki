"""Postgres-backed image blob storage seam."""

from __future__ import annotations

import hashlib
import uuid

from pydantic import BaseModel
from sqlalchemy import delete as sqla_delete
from sqlalchemy import select
from sqlalchemy.orm import defer

from app.db.models import Image
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


def delete(image_id: str) -> bool:
    # Statement delete so the blob column is never fetched just to drop the row.
    with session() as s:
        stmt = (
            sqla_delete(Image)
            .where(Image.id == image_id)
            .execution_options(synchronize_session=False)
        )
        return execute_dml(s, stmt) > 0
