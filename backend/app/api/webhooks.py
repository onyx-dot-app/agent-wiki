"""Inbound webhooks — generic event sink that fans out to triggers/agents."""
from __future__ import annotations

import logging

from flask import Blueprint

bp = Blueprint("webhooks", __name__)
log = logging.getLogger(__name__)


@bp.post("/<source>")
def receive(source: str):
    # No login_required — webhooks authenticate via per-source signing secrets.
    # TODO: verify signature, record event, enqueue downstream work.
    log.info("webhook received from %s (unimplemented)", source)
    raise NotImplementedError
