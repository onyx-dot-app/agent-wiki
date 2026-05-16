"""FastAPI router for external document ingestion (/api/documents/*)."""
from __future__ import annotations

import logging
import secrets

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import JSONResponse

from app.ingest import settings as ingest_settings
from app.models._helpers import RequestError
from app.models.file_system import (
    IngestRequest,
    IngestResponse,
    IngestTooLargeResponse,
)
from app.tasks.queues import QueueFullError
from app.tasks.wiki_update import process_pushed_document

log = logging.getLogger(__name__)

router = APIRouter()


def _require_ingest_key(request: Request) -> None:
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")
    token = auth[len("Bearer "):]
    stored = ingest_settings.get().api_key
    if not stored or not secrets.compare_digest(token, stored):
        raise HTTPException(status_code=401, detail="invalid api key")


@router.post(
    "/ingest",
    response_model=IngestResponse,
    status_code=status.HTTP_202_ACCEPTED,
    responses={413: {"model": IngestTooLargeResponse}},
)
def ingest_update(request: Request, req: IngestRequest) -> IngestResponse | JSONResponse:
    """Receive a document push from an external system. Returns 202
    on enqueue, 413 on size overflow."""
    _require_ingest_key(request)
    if not req.content.strip():
        raise RequestError("content is required and must be a non-empty string")

    max_chars = ingest_settings.get().max_doc_chars
    total_chars = len(req.content) + (len(req.diff) if req.diff else 0)
    if total_chars > max_chars:
        too_large = IngestTooLargeResponse(
            error=(
                f"document too large: {total_chars} chars exceeds the configured "
                f"max of {max_chars} (set on /admin/ingest)"
            ),
            limit=max_chars,
            received=total_chars,
        )
        return JSONResponse(status_code=413, content=too_large.model_dump())

    push = req.model_dump()
    try:
        result = process_pushed_document(push)
    except QueueFullError:
        raise
    except Exception as exc:
        log.exception(
            "failed to enqueue process_pushed_document source=%s", req.source,
        )
        raise HTTPException(
            status_code=503, detail="failed to enqueue background task",
        ) from exc
    task_id = getattr(result, "id", None)
    log.info(
        "ingest enqueued source=%s title=%s len=%d task_id=%s",
        req.source, req.title, total_chars, task_id,
    )
    return IngestResponse(queued=True, task_id=task_id)
