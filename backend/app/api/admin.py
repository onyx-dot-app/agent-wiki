"""Admin endpoints — user management + LLM settings. Gated on is_admin."""
from __future__ import annotations

import logging

from typing import Any

from flask import Blueprint, jsonify, request

from app.auth import admin_required, current_user, users as users_repo
from app.ingest import settings as ingest_settings
from app.ingest.settings import IngestSettings
from app.llm import settings as llm_settings
from app.llm.settings import LLMSettings
from app.models._helpers import error, parse_body
from app.models.admin import (
    AdminUserListResponse,
    AdminUserView,
    BraintrustConfigRequest,
    BraintrustView,
    IngestConfigRequest,
    IngestView,
    LLMConfigRequest,
    LLMView,
    OkResponse,
    UpdateUserRequest,
    WebConfigRequest,
    WebView,
)
from app.tracing import settings as braintrust_settings
from app.tracing.settings import BraintrustSettings
from app.web import settings as web_settings
from app.web.settings import WebSettings

bp = Blueprint("admin", __name__)
log = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Users
# --------------------------------------------------------------------------- #


def _user_view(row: dict[str, Any]) -> AdminUserView:
    return AdminUserView(
        id=row["id"],
        email=row["email"],
        name=row["name"],
        is_admin=bool(row["is_admin"]),
        created_at=row["created_at"],
    )


@bp.get("/users")
@admin_required
def list_users():
    return jsonify(AdminUserListResponse(
        users=[_user_view(r) for r in users_repo.list_all()],
    ).model_dump())


@bp.patch("/users/<user_id>")
@admin_required
def update_user(user_id: str):
    req = parse_body(UpdateUserRequest, request.get_json(silent=True))
    target = users_repo.get_by_id(user_id)
    if target is None:
        return error("not found", 404)
    if not req.is_admin and bool(target["is_admin"]) and users_repo.admin_count() <= 1:
        return error("cannot demote the last admin", 400)
    users_repo.set_admin(user_id, req.is_admin)
    actor = current_user()
    log.info(
        "admin: %s set is_admin=%s on user %s",
        actor.id if actor else "?", req.is_admin, user_id,
    )
    row = users_repo.get_by_id(user_id)
    assert row is not None
    return jsonify(_user_view(row).model_dump())


@bp.delete("/users/<user_id>")
@admin_required
def delete_user(user_id: str):
    me = current_user()
    assert me is not None
    if me.id == user_id:
        return error("cannot delete yourself", 400)
    target = users_repo.get_by_id(user_id)
    if target is None:
        return error("not found", 404)
    if bool(target["is_admin"]) and users_repo.admin_count() <= 1:
        return error("cannot delete the last admin", 400)
    users_repo.delete(user_id)
    log.info("admin: %s deleted user %s", me.id, user_id)
    return jsonify(OkResponse().model_dump())


# --------------------------------------------------------------------------- #
# LLM settings
# --------------------------------------------------------------------------- #


def _redact(key: str) -> str:
    if not key:
        return ""
    if len(key) <= 8:
        return "•" * len(key)
    return f"{key[:4]}…{key[-4:]}"


_ALLOWED_PROVIDERS = ("anthropic", "openai", "gemini", "ollama")


def _llm_view(s: LLMSettings) -> LLMView:
    return LLMView(
        provider=s.provider,
        model=s.model,
        anthropic_api_key_set=bool(s.anthropic_api_key),
        openai_api_key_set=bool(s.openai_api_key),
        gemini_api_key_set=bool(s.gemini_api_key),
        anthropic_api_key_hint=_redact(s.anthropic_api_key),
        openai_api_key_hint=_redact(s.openai_api_key),
        gemini_api_key_hint=_redact(s.gemini_api_key),
        ollama_base_url=s.ollama_base_url,
    )


@bp.get("/llm")
@admin_required
def get_llm():
    return jsonify(_llm_view(llm_settings.get()).model_dump())


@bp.put("/llm")
@admin_required
def put_llm():
    raw: dict[str, Any] = request.get_json(silent=True) or {}
    req = parse_body(LLMConfigRequest, raw)
    provider = req.provider.strip().lower()
    model = req.model.strip()
    if provider not in _ALLOWED_PROVIDERS:
        allowed = ", ".join(f"'{p}'" for p in _ALLOWED_PROVIDERS)
        return error(f"provider must be one of {allowed}", 400)
    if not model:
        return error("model is required", 400)

    current = llm_settings.get()

    # Empty string means "leave existing secret untouched"; explicit null
    # clears it. Same convention for the Ollama base URL even though it
    # isn't a secret, so the form can submit "no change" without echoing
    # back stored config.
    def _resolve_secret(field: str, sent: str | None, existing: str) -> str:
        if field not in raw:
            return existing
        if sent is None:
            return ""
        if sent == "":
            return existing
        return sent

    anthropic_key = _resolve_secret("anthropic_api_key", req.anthropic_api_key, current.anthropic_api_key)
    openai_key = _resolve_secret("openai_api_key", req.openai_api_key, current.openai_api_key)
    gemini_key = _resolve_secret("gemini_api_key", req.gemini_api_key, current.gemini_api_key)
    ollama_base_url = _resolve_secret("ollama_base_url", req.ollama_base_url, current.ollama_base_url)

    llm_settings.upsert(
        provider=provider,
        model=model,
        anthropic_api_key=anthropic_key,
        openai_api_key=openai_key,
        gemini_api_key=gemini_key,
        ollama_base_url=ollama_base_url,
    )
    actor = current_user()
    log.info(
        "admin: %s updated llm settings provider=%s model=%s",
        actor.id if actor else "?", provider, model,
    )
    return jsonify(_llm_view(llm_settings.get()).model_dump())


# --------------------------------------------------------------------------- #
# Web search / crawl settings (Serper + Firecrawl)
# --------------------------------------------------------------------------- #


def _web_view(s: WebSettings) -> WebView:
    return WebView(
        serper_api_key_set=bool(s.serper_api_key),
        firecrawl_api_key_set=bool(s.firecrawl_api_key),
        serper_api_key_hint=_redact(s.serper_api_key),
        firecrawl_api_key_hint=_redact(s.firecrawl_api_key),
    )


@bp.get("/web")
@admin_required
def get_web():
    return jsonify(_web_view(web_settings.get()).model_dump())


@bp.put("/web")
@admin_required
def put_web():
    raw: dict[str, Any] = request.get_json(silent=True) or {}
    req = parse_body(WebConfigRequest, raw)
    current = web_settings.get()

    # Empty string means "leave existing key untouched"; explicit null clears it.
    def _resolve(field: str, sent: str | None, existing: str) -> str:
        if field not in raw:
            return existing
        if sent is None:
            return ""
        if sent == "":
            return existing
        return sent

    serper_key = _resolve("serper_api_key", req.serper_api_key, current.serper_api_key)
    firecrawl_key = _resolve("firecrawl_api_key", req.firecrawl_api_key, current.firecrawl_api_key)

    web_settings.upsert(
        serper_api_key=serper_key,
        firecrawl_api_key=firecrawl_key,
    )
    actor = current_user()
    log.info(
        "admin: %s updated web settings serper_set=%s firecrawl_set=%s",
        actor.id if actor else "?", bool(serper_key), bool(firecrawl_key),
    )
    return jsonify(_web_view(web_settings.get()).model_dump())


# --------------------------------------------------------------------------- #
# Ingest settings (inbound document push from external systems)
# --------------------------------------------------------------------------- #


# Floor at 1k chars (smaller than this and meaningful docs get rejected) and
# cap at 5M (single LLM context budget; anything larger is almost certainly a
# misconfiguration on the pushing side).
_MIN_DOC_CHARS = 1_000
_MAX_DOC_CHARS = 5_000_000


def _ingest_view(s: IngestSettings) -> IngestView:
    return IngestView(max_doc_chars=s.max_doc_chars)


@bp.get("/ingest")
@admin_required
def get_ingest():
    return jsonify(_ingest_view(ingest_settings.get()).model_dump())


@bp.put("/ingest")
@admin_required
def put_ingest():
    req = parse_body(IngestConfigRequest, request.get_json(silent=True))
    if req.max_doc_chars < _MIN_DOC_CHARS or req.max_doc_chars > _MAX_DOC_CHARS:
        return error(f"max_doc_chars must be between {_MIN_DOC_CHARS} and {_MAX_DOC_CHARS}", 400)
    ingest_settings.upsert(max_doc_chars=req.max_doc_chars)
    actor = current_user()
    log.info(
        "admin: %s updated ingest settings max_doc_chars=%d",
        actor.id if actor else "?", req.max_doc_chars,
    )
    return jsonify(_ingest_view(ingest_settings.get()).model_dump())


# --------------------------------------------------------------------------- #
# Braintrust tracing settings                                                 #
# --------------------------------------------------------------------------- #


def _braintrust_view(s: BraintrustSettings) -> BraintrustView:
    return BraintrustView(
        project=s.project,
        api_key_set=bool(s.api_key),
        api_key_hint=_redact(s.api_key),
        enabled=s.enabled,
    )


@bp.get("/braintrust")
@admin_required
def get_braintrust():
    return jsonify(_braintrust_view(braintrust_settings.get()).model_dump())


@bp.put("/braintrust")
@admin_required
def put_braintrust():
    raw: dict[str, Any] = request.get_json(silent=True) or {}
    req = parse_body(BraintrustConfigRequest, raw)
    project = req.project.strip()
    current = braintrust_settings.get()

    # Same convention as the LLM settings: empty string = "leave existing
    # untouched"; explicit null = "clear". For non-secret fields (project,
    # enabled) the request value is the authoritative one.
    def _resolve_secret(field: str, sent: str | None, existing: str) -> str:
        if field not in raw:
            return existing
        if sent is None:
            return ""
        if sent == "":
            return existing
        return sent

    api_key = _resolve_secret("api_key", req.api_key, current.api_key)
    # Tracing can only be enabled when both project and key are set —
    # mirrors the UI gating but is also enforced server-side so a stale
    # form can't flip it on incorrectly.
    enabled = bool(req.enabled and project and api_key)

    braintrust_settings.upsert(project=project, api_key=api_key, enabled=enabled)
    # The tracing module caches its logger keyed by (project, api_key), so
    # rotating credentials transparently picks up the new value on the
    # next call. Toggling ``enabled`` without changing credentials short-
    # circuits before the cache lookup, so no explicit invalidation needed.
    actor = current_user()
    log.info(
        "admin: %s updated braintrust settings project=%s key_set=%s enabled=%s",
        actor.id if actor else "?", project, bool(api_key), enabled,
    )
    return jsonify(_braintrust_view(braintrust_settings.get()).model_dump())
