"""FastAPI port of ``app/api/admin.py`` (Phase 3)."""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from app.auth import User, users as users_repo
from app.auth.deps import require_admin
from app.ingest import settings as ingest_settings
from app.ingest.settings import IngestSettings
from app.llm import settings as llm_settings
from app.llm.settings import LLMSettings
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

router = APIRouter()
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


@router.get("/users", response_model=AdminUserListResponse)
def list_users(_actor: User = Depends(require_admin)) -> AdminUserListResponse:
    return AdminUserListResponse(
        users=[_user_view(r) for r in users_repo.list_all()],
    )


@router.patch("/users/{user_id}", response_model=AdminUserView)
def update_user(
    user_id: str,
    req: UpdateUserRequest,
    actor: User = Depends(require_admin),
) -> AdminUserView:
    target = users_repo.get_by_id(user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="not found")
    if not req.is_admin and bool(target["is_admin"]) and users_repo.admin_count() <= 1:
        raise HTTPException(status_code=400, detail="cannot demote the last admin")
    users_repo.set_admin(user_id, req.is_admin)
    log.info(
        "admin: %s set is_admin=%s on user %s", actor.id, req.is_admin, user_id,
    )
    row = users_repo.get_by_id(user_id)
    assert row is not None
    return _user_view(row)


@router.delete("/users/{user_id}", response_model=OkResponse)
def delete_user(user_id: str, actor: User = Depends(require_admin)) -> OkResponse:
    if actor.id == user_id:
        raise HTTPException(status_code=400, detail="cannot delete yourself")
    target = users_repo.get_by_id(user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="not found")
    if bool(target["is_admin"]) and users_repo.admin_count() <= 1:
        raise HTTPException(status_code=400, detail="cannot delete the last admin")
    users_repo.delete(user_id)
    log.info("admin: %s deleted user %s", actor.id, user_id)
    return OkResponse()


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
        provider_models=s.provider_models,
    )


@router.get("/llm", response_model=LLMView)
def get_llm(_actor: User = Depends(require_admin)) -> LLMView:
    return _llm_view(llm_settings.get())


@router.put("/llm", response_model=LLMView)
def put_llm(
    req: LLMConfigRequest,
    actor: User = Depends(require_admin),
) -> LLMView:
    # ``req.model_fields_set`` distinguishes "client omitted this field"
    # from "client sent null/empty" — required for the
    # empty-string-means-keep convention below.
    sent_fields = req.model_fields_set
    current = llm_settings.get()

    if "provider" in sent_fields or "model" in sent_fields:
        provider = (req.provider or "").strip().lower()
        model = (req.model or "").strip()
        if provider not in _ALLOWED_PROVIDERS:
            allowed = ", ".join(f"'{p}'" for p in _ALLOWED_PROVIDERS)
            raise HTTPException(status_code=400, detail=f"provider must be one of {allowed}")
        if not model:
            raise HTTPException(status_code=400, detail="model is required")
    else:
        provider = current.provider
        model = current.model

    def _resolve_secret(field: str, sent: str | None, existing: str) -> str:
        if field not in sent_fields:
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

    new_provider_models = req.provider_models if "provider_models" in sent_fields else None

    llm_settings.upsert(
        provider=provider,
        model=model,
        anthropic_api_key=anthropic_key,
        openai_api_key=openai_key,
        gemini_api_key=gemini_key,
        ollama_base_url=ollama_base_url,
        provider_models=new_provider_models,
    )
    log.info(
        "admin: %s updated llm settings provider=%s model=%s",
        actor.id, provider, model,
    )
    return _llm_view(llm_settings.get())


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


@router.get("/web", response_model=WebView)
def get_web(_actor: User = Depends(require_admin)) -> WebView:
    return _web_view(web_settings.get())


@router.put("/web", response_model=WebView)
def put_web(
    req: WebConfigRequest,
    actor: User = Depends(require_admin),
) -> WebView:
    sent_fields = req.model_fields_set
    current = web_settings.get()

    def _resolve(field: str, sent: str | None, existing: str) -> str:
        if field not in sent_fields:
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
    log.info(
        "admin: %s updated web settings serper_set=%s firecrawl_set=%s",
        actor.id, bool(serper_key), bool(firecrawl_key),
    )
    return _web_view(web_settings.get())


# --------------------------------------------------------------------------- #
# Ingest settings (inbound document push from external systems)
# --------------------------------------------------------------------------- #


_MIN_DOC_CHARS = 1_000
_MAX_DOC_CHARS = 5_000_000


def _ingest_view(s: IngestSettings) -> IngestView:
    return IngestView(max_doc_chars=s.max_doc_chars)


@router.get("/ingest", response_model=IngestView)
def get_ingest(_actor: User = Depends(require_admin)) -> IngestView:
    return _ingest_view(ingest_settings.get())


@router.put("/ingest", response_model=IngestView)
def put_ingest(
    req: IngestConfigRequest, actor: User = Depends(require_admin),
) -> IngestView:
    if req.max_doc_chars < _MIN_DOC_CHARS or req.max_doc_chars > _MAX_DOC_CHARS:
        raise HTTPException(
            status_code=400,
            detail=f"max_doc_chars must be between {_MIN_DOC_CHARS} and {_MAX_DOC_CHARS}",
        )
    ingest_settings.upsert(max_doc_chars=req.max_doc_chars)
    log.info(
        "admin: %s updated ingest settings max_doc_chars=%d",
        actor.id, req.max_doc_chars,
    )
    return _ingest_view(ingest_settings.get())


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


@router.get("/braintrust", response_model=BraintrustView)
def get_braintrust(_actor: User = Depends(require_admin)) -> BraintrustView:
    return _braintrust_view(braintrust_settings.get())


@router.put("/braintrust", response_model=BraintrustView)
def put_braintrust(
    req: BraintrustConfigRequest,
    actor: User = Depends(require_admin),
) -> BraintrustView:
    sent_fields = req.model_fields_set
    project = req.project.strip()
    current = braintrust_settings.get()

    def _resolve_secret(field: str, sent: str | None, existing: str) -> str:
        if field not in sent_fields:
            return existing
        if sent is None:
            return ""
        if sent == "":
            return existing
        return sent

    api_key = _resolve_secret("api_key", req.api_key, current.api_key)
    enabled = bool(req.enabled and project and api_key)

    braintrust_settings.upsert(project=project, api_key=api_key, enabled=enabled)
    log.info(
        "admin: %s updated braintrust settings project=%s key_set=%s enabled=%s",
        actor.id, project, bool(api_key), enabled,
    )
    return _braintrust_view(braintrust_settings.get())
