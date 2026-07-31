"""Image upload and serving API for wiki pages."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from starlette.concurrency import run_in_threadpool

from app.auth import User, require_can
from app.auth.deps import require_user, require_user_or_bearer
from app.metrics import wiki_image_upload_rejected_total
from app.models.images import UploadImageResponse
from app.wiki import doc_ids, filesystem, image_store, image_upload

router = APIRouter()


@router.post("", response_model=UploadImageResponse)
async def upload_image(
    request: Request,
    path: str = "",
    filename: str | None = None,
    user: User = Depends(require_user),
) -> UploadImageResponse:
    # Cross-site POSTs arrive without the session cookie (SameSite=lax, app/main.py), so require_user rejects them.
    try:
        anchor = image_upload.validate_anchor(path)
    except image_upload.ImageUploadError as e:
        raise HTTPException(status_code=e.status, detail=e.message) from e

    # Streamed rather than handed to the shared path whole, so an oversized
    # body is refused mid-flight instead of after it is all in memory.
    buf = bytearray()
    async for chunk in request.stream():
        if len(buf) + len(chunk) > image_upload.UPLOAD_CAP_BYTES:
            wiki_image_upload_rejected_total.labels(reason="too_large").inc()
            raise HTTPException(status_code=413, detail="image exceeds 10 MiB limit")
        buf.extend(chunk)

    try:
        return await run_in_threadpool(
            image_upload.store,
            data=bytes(buf),
            anchor=anchor,
            filename=filename,
            user=user,
        )
    except image_upload.ImageUploadError as e:
        if e.reason:
            wiki_image_upload_rejected_total.labels(reason=e.reason).inc()
        raise HTTPException(status_code=e.status, detail=e.message) from e


@router.get("/{image_id}")
def serve_image(
    image_id: str,
    request: Request,
    user: User = Depends(require_user_or_bearer),
) -> Response:
    meta = image_store.stat(image_id)
    if meta is None:
        raise HTTPException(status_code=404, detail="not found")
    row = doc_ids.resolve(meta.anchor_doc_id)
    if row is None or row["deleted_at"] is not None:
        raise HTTPException(status_code=404, detail="not found")
    try:
        rel = filesystem.safe_rel_path(str(row["path"]))
    except ValueError as e:
        raise HTTPException(status_code=404, detail="not found") from e
    require_can("read", rel, user)

    etag = f'"{meta.sha256}"'
    raw_if_none_match = request.headers.get("if-none-match", "")
    if_none_match = {candidate.strip() for candidate in raw_if_none_match.split(",") if candidate.strip()}
    if "*" in if_none_match or etag in if_none_match:
        return Response(
            status_code=304,
            headers={"ETag": etag, "Cache-Control": "private, no-cache"},
        )

    rec = image_store.get(image_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="not found")
    return Response(
        content=rec.data,
        media_type=meta.content_type,
        headers={
            "ETag": etag,
            "Cache-Control": "private, no-cache",
            "X-Content-Type-Options": "nosniff",
        },
    )
