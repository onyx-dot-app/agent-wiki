"""Inbound webhooks — generic event sink that fans out to triggers/agents."""
from __future__ import annotations

from flask import Blueprint, jsonify, request

bp = Blueprint("webhooks", __name__)


@bp.post("/<source>")
def receive(source: str):
    # No login_required — webhooks authenticate via per-source signing secrets.
    # TODO: verify signature, record event, enqueue downstream work.
    raise NotImplementedError
