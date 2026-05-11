"""FastAPI port of ``app/api/webhooks.py`` (Phase 2). v0 stub."""
from __future__ import annotations

import logging

from fastapi import APIRouter

router = APIRouter()
log = logging.getLogger(__name__)


@router.post("/{source}")
def receive(source: str) -> None:
    # No auth — webhooks authenticate via per-source signing secrets.
    # TODO: verify signature, record event, enqueue downstream work.
    log.info("webhook received from %s (unimplemented)", source)
    raise NotImplementedError
