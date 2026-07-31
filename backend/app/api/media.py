"""Media upload and serving API for wiki pages."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from starlette.concurrency import run_in_threadpool

from app.auth import User, require_can
from app.auth.deps import require_user, require_user_or_bearer
from app.metrics import wiki_media_upload_rejected_total
from app.models.media import UploadMediaResponse
from app.wiki import doc_ids, filesystem, media_store, media_upload

router = APIRouter()


@router.post("", response_model=UploadMediaResponse)
async def upload_media(
    request: Request,
    path: str = "",
    filename: str | None = None,
    user: User = Depends(require_user),
) -> UploadMediaResponse:
    # Session-cookie auth only, so a cross-site POST carries no credential.
    try:
        anchor = media_upload.validate_anchor(path)
    except media_upload.MediaUploadError as e:
        raise HTTPException(status_code=e.status, detail=e.message) from e

    # Refused on the declared length first, before any body is read. Abandoning
    # a stream mid-upload closes the connection under the client, which sees a
    # transport error instead of this status.
    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit():
        if int(declared) > media_upload.UPLOAD_CAP_BYTES:
            wiki_media_upload_rejected_total.labels(reason="too_large").inc()
            raise HTTPException(
                status_code=413, detail=media_upload.TOO_LARGE_MESSAGE
            )

    # A chunked upload declares no length, so the cap still has to hold while
    # streaming. That path does break the connection, which is why the header
    # check above exists.
    buf = bytearray()
    async for chunk in request.stream():
        if len(buf) + len(chunk) > media_upload.UPLOAD_CAP_BYTES:
            wiki_media_upload_rejected_total.labels(reason="too_large").inc()
            raise HTTPException(
                status_code=413, detail=media_upload.TOO_LARGE_MESSAGE
            )
        buf.extend(chunk)

    try:
        return await run_in_threadpool(
            media_upload.store,
            data=bytes(buf),
            anchor=anchor,
            filename=filename,
            user=user,
        )
    except media_upload.MediaUploadError as e:
        if e.reason:
            wiki_media_upload_rejected_total.labels(reason=e.reason).inc()
        raise HTTPException(status_code=e.status, detail=e.message) from e


@router.get("/{media_id}")
def serve_media(
    media_id: str,
    request: Request,
    user: User = Depends(require_user_or_bearer),
) -> Response:
    meta = media_store.stat(media_id)
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

    rec = media_store.get(media_id)
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
