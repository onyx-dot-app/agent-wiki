"""Image upload and serving API for wiki pages."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from starlette.concurrency import run_in_threadpool

from app.auth import User, require_can
from app.auth.deps import require_user, require_user_or_bearer
from app.models.images import UploadImageResponse
from app.wiki import doc_ids, filesystem, image_store

router = APIRouter()

_UPLOAD_CAP_BYTES = 10 * 1024 * 1024


@router.post("", response_model=UploadImageResponse)
async def upload_image(
    request: Request,
    path: str = "",
    filename: str | None = None,
    user: User = Depends(require_user),
) -> UploadImageResponse:
    # Cross-site POSTs arrive without the session cookie (SameSite=lax, app/main.py), so require_user rejects them.
    if not path.strip():
        raise HTTPException(status_code=400, detail="path required")
    try:
        rel = filesystem.safe_rel_path(path)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    await run_in_threadpool(require_can, "write", rel, user)

    buf = bytearray()
    async for chunk in request.stream():
        if len(buf) + len(chunk) > _UPLOAD_CAP_BYTES:
            raise HTTPException(status_code=413, detail="image exceeds 10 MiB limit")
        buf.extend(chunk)
    data = bytes(buf)
    if not data:
        raise HTTPException(status_code=400, detail="empty image body")

    sniffed = image_store.sniff_image_type(data)
    if sniffed is None:
        raise HTTPException(status_code=415, detail="unsupported image type")
    declared = (request.headers.get("content-type") or "").split(";")[0].strip().lower()
    if declared != sniffed:
        raise HTTPException(status_code=415, detail="content-type does not match image data")

    anchor_doc_id = await run_in_threadpool(doc_ids.get_or_mint, rel)
    image_id = await run_in_threadpool(
        image_store.put,
        data=data,
        content_type=sniffed,
        anchor_doc_id=anchor_doc_id,
        uploaded_by=user.id,
    )

    url = f"/api/wiki/images/{image_id}"
    alt = (filename or "").replace("\n", " ").replace("\r", " ").replace("]", "")
    return UploadImageResponse(id=image_id, url=url, markdown=f"![{alt}]({url})")


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
