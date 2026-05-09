"""LLM status endpoint — readable by any logged-in user.

Distinct from ``app.api.admin``'s ``/admin/llm`` (admin-only, full settings
view): this exposes only whether the system has a usable LLM configured,
so the frontend can surface a setup banner to non-admins without leaking
keys or model names.
"""
from __future__ import annotations

from flask import Blueprint, jsonify

from app.auth import login_required
from app.llm import providers as llm_providers
from app.llm import settings as llm_settings
from app.llm.errors import LLMError
from app.models.llm import LLMStatusResponse

bp = Blueprint("llm", __name__)


@bp.get("/status")
@login_required
def status():
    s = llm_settings.get()
    provider = llm_providers.get(s.provider) if s.provider else None
    configured = False
    if provider is not None and s.model:
        try:
            provider.check_configured(s)
            configured = True
        except LLMError:
            configured = False
    return jsonify(LLMStatusResponse(
        configured=configured,
        provider=s.provider,
    ).model_dump())
