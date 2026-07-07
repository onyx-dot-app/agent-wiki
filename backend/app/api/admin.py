"""FastAPI port of ``app/api/admin.py`` (Phase 3)."""

from __future__ import annotations

import csv
import io
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from app.auth import User, users as users_repo
from app.auth import groups as groups_repo
from app.auth import invites
from app.auth.deps import require_admin
from app.ingest import settings as ingest_settings
from app.ingest.settings import IngestSettings
from app.llm.errors import LLMError
from app.llm import providers as llm_providers
from app.llm import settings as llm_settings
from app.llm.settings import LLMSettings
from app.models.admin import (
    AdminUserListResponse,
    AdminUserView,
    BraintrustConfigRequest,
    BraintrustView,
    IngestConfigRequest,
    InviteUsersRequest,
    InvitedUserView,
    IngestView,
    RegenerateKeyResponse,
    LLMConfigRequest,
    LLMView,
    OkResponse,
    ProviderTestRequest,
    ProviderTestResult,
    UpdateUserRequest,
    UserCounts,
    WebConfigRequest,
    WebView,
    EmailSmtpConfigRequest,
    EmailSmtpView,
    EmailTestRequest,
    EmailTestResponse,
    SlackAppConfigRequest,
    SlackAppView,
)
from app.onyx.client import validate_onyx_base_url
from app.email import service as email_service
from app.email import settings as email_settings
from app.email.service import EmailSendError
from app.email.settings import EmailSmtpSettings as EmailSmtpSettingsModel
from app.slack import app_settings as slack_app_settings
from app.slack.app_settings import SlackAppSettings as SlackAppSettingsModel
from app.tracing import settings as braintrust_settings
from app.tracing.settings import BraintrustSettings
from app.web import settings as web_settings
from app.web.settings import WebSettings

router = APIRouter()
log = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Users
# --------------------------------------------------------------------------- #


def _user_view(row: dict[str, Any], groups: list[str] | None = None) -> AdminUserView:
    is_active = bool(row["is_active"])
    return AdminUserView(
        id=row["id"],
        email=row["email"],
        name=row["name"],
        is_admin=bool(row["is_admin"]),
        is_active=is_active,
        status="active" if is_active else "inactive",
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        groups=groups or [],
    )


@router.get("/users", response_model=AdminUserListResponse)
def list_users(_actor: User = Depends(require_admin)) -> AdminUserListResponse:
    by_user = groups_repo.groups_by_user()
    users = [_user_view(r, by_user.get(r["id"], [])) for r in users_repo.list_all()]
    invited = [InvitedUserView(email=e) for e in invites.list_emails()]
    status = users_repo.status_counts()
    return AdminUserListResponse(
        users=users,
        invited=invited,
        counts=UserCounts(
            active=status["active"],
            inactive=status["inactive"],
            invited=len(invited),
        ),
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

    was_admin = bool(target["is_admin"])
    was_active = bool(target["is_active"])
    # Effective state after this PATCH (fields default to current value).
    final_admin = req.is_admin if req.is_admin is not None else was_admin
    final_active = req.is_active if req.is_active is not None else was_active

    # Validate every guard BEFORE mutating, so a rejected request never leaves
    # a half-applied change. Guards check the *resulting* state, so e.g.
    # demoting + deactivating in one call doesn't false-trip the last-admin
    # check on a stale snapshot.
    if req.is_active is False and user_id == actor.id:
        raise HTTPException(status_code=400, detail="cannot deactivate yourself")

    # An active admin being demoted and/or deactivated must not be the last one.
    if (was_admin and was_active) and not (final_admin and final_active):
        if users_repo.admin_count() <= 1:
            detail = (
                "cannot demote the last admin"
                if not final_admin
                else "cannot deactivate the last admin"
            )
            raise HTTPException(status_code=400, detail=detail)

    if req.is_admin is not None and req.is_admin != was_admin:
        users_repo.set_admin(user_id, req.is_admin)
        log.info("admin: %s set is_admin=%s on user %s", actor.id, req.is_admin, user_id)

    if req.is_active is not None and req.is_active != was_active:
        users_repo.set_active(user_id, req.is_active)
        log.info("admin: %s set is_active=%s on user %s", actor.id, req.is_active, user_id)

    row = users_repo.get_by_id(user_id)
    assert row is not None
    return _user_view(row, groups_repo.groups_by_user().get(user_id, []))


@router.put("/users/invite", response_model=AdminUserListResponse)
def invite_users(
    req: InviteUsersRequest,
    actor: User = Depends(require_admin),
) -> AdminUserListResponse:
    added = invites.add(req.emails, invited_by_user_id=actor.id)
    log.info("admin: %s invited %d email(s)", actor.id, len(added))
    return list_users(actor)


@router.delete("/users/invited", response_model=OkResponse)
def cancel_invite(
    email: str = Query(...),
    actor: User = Depends(require_admin),
) -> OkResponse:
    invites.remove(email)
    log.info("admin: %s cancelled invite for %s", actor.id, email.strip().lower())
    return OkResponse()


@router.get("/users/download")
def download_users_csv(_actor: User = Depends(require_admin)) -> Response:
    by_user = groups_repo.groups_by_user()
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["email", "name", "role", "status", "groups", "created_at"])
    for r in users_repo.list_all():
        writer.writerow(
            [
                r["email"],
                r["name"] or "",
                "admin" if r["is_admin"] else "basic",
                "active" if r["is_active"] else "inactive",
                "; ".join(by_user.get(r["id"], [])),
                r["created_at"],
            ]
        )
    for email in invites.list_emails():
        writer.writerow([email, "", "basic", "invited", "", ""])
    return Response(
        content=buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="users.csv"'},
    )


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
    """First4…last4 only when the value is long enough that the hint can't
    reconstruct it; fixed width below that so neither content nor length leak."""
    if not key:
        return ""
    if len(key) < 16:
        return "••••••••"
    return f"{key[:4]}…{key[-4:]}"


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
        custom_api_key_set=bool(s.custom_api_key),
        custom_api_key_hint=_redact(s.custom_api_key),
        custom_base_url=s.custom_base_url,
        custom_display_name=s.custom_display_name,
        bedrock_aws_region=s.bedrock_aws_region,
        bedrock_endpoint_url=s.bedrock_endpoint_url,
        bedrock_aws_access_key_id_set=bool(s.bedrock_aws_access_key_id),
        bedrock_aws_access_key_id_hint=_redact(s.bedrock_aws_access_key_id),
        bedrock_aws_secret_access_key_set=bool(s.bedrock_aws_secret_access_key),
        bedrock_aws_secret_access_key_hint=_redact(s.bedrock_aws_secret_access_key),
        bedrock_aws_session_token_set=bool(s.bedrock_aws_session_token),
        bedrock_aws_bearer_token_set=bool(s.bedrock_aws_bearer_token),
        bedrock_aws_bearer_token_hint=_redact(s.bedrock_aws_bearer_token),
        provider_models=s.provider_models,
        ingest_selector_model=s.ingest_selector_model,
    )


def _normalize_custom_base_url(raw: str) -> str:
    url = raw.strip().rstrip("/")
    if url:
        if not url.startswith(("http://", "https://")):
            raise HTTPException(
                status_code=400,
                detail="custom_base_url must start with http:// or https://",
            )
        if url.endswith("/chat/completions"):
            raise HTTPException(
                status_code=400,
                detail="custom_base_url should be the API base (e.g. https://host/v1) — requests append /chat/completions automatically",
            )
    return url


def _normalize_bedrock_endpoint(raw: str) -> str:
    url = raw.strip().rstrip("/")
    if url and not url.startswith(("http://", "https://")):
        raise HTTPException(
            status_code=400,
            detail="bedrock_endpoint_url must start with http:// or https://",
        )
    return url


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
        allowed = llm_providers.names()
        if provider not in allowed:
            allowed_str = ", ".join(f"'{p}'" for p in allowed)
            raise HTTPException(status_code=400, detail=f"provider must be one of {allowed_str}")
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

    anthropic_key = _resolve_secret(
        "anthropic_api_key", req.anthropic_api_key, current.anthropic_api_key
    )
    openai_key = _resolve_secret("openai_api_key", req.openai_api_key, current.openai_api_key)
    gemini_key = _resolve_secret("gemini_api_key", req.gemini_api_key, current.gemini_api_key)
    ollama_base_url = _resolve_secret(
        "ollama_base_url", req.ollama_base_url, current.ollama_base_url
    )
    custom_api_key = _resolve_secret("custom_api_key", req.custom_api_key, current.custom_api_key)
    custom_base_url = _normalize_custom_base_url(
        _resolve_secret("custom_base_url", req.custom_base_url, current.custom_base_url)
    )

    if "custom_display_name" in sent_fields:
        custom_display_name = (req.custom_display_name or "").strip()
    else:
        custom_display_name = current.custom_display_name

    # Region + endpoint aren't secrets, but they follow the same blank=keep /
    # null=clear convention as the other URL fields (ollama/custom_base_url).
    bedrock_aws_region = _resolve_secret(
        "bedrock_aws_region", req.bedrock_aws_region, current.bedrock_aws_region
    ).strip()
    bedrock_endpoint_url = _normalize_bedrock_endpoint(
        _resolve_secret(
            "bedrock_endpoint_url", req.bedrock_endpoint_url, current.bedrock_endpoint_url
        )
    )
    bedrock_access_key_id = _resolve_secret(
        "bedrock_aws_access_key_id",
        req.bedrock_aws_access_key_id,
        current.bedrock_aws_access_key_id,
    )
    bedrock_secret_access_key = _resolve_secret(
        "bedrock_aws_secret_access_key",
        req.bedrock_aws_secret_access_key,
        current.bedrock_aws_secret_access_key,
    )
    bedrock_session_token = _resolve_secret(
        "bedrock_aws_session_token",
        req.bedrock_aws_session_token,
        current.bedrock_aws_session_token,
    )
    bedrock_bearer_token = _resolve_secret(
        "bedrock_aws_bearer_token",
        req.bedrock_aws_bearer_token,
        current.bedrock_aws_bearer_token,
    )

    new_provider_models = req.provider_models if "provider_models" in sent_fields else None

    if "ingest_selector_model" in sent_fields:
        ingest_selector_model = (req.ingest_selector_model or "").strip()
    else:
        ingest_selector_model = current.ingest_selector_model

    llm_settings.upsert(
        provider=provider,
        model=model,
        anthropic_api_key=anthropic_key,
        openai_api_key=openai_key,
        gemini_api_key=gemini_key,
        ollama_base_url=ollama_base_url,
        custom_api_key=custom_api_key,
        custom_base_url=custom_base_url,
        custom_display_name=custom_display_name,
        bedrock_aws_region=bedrock_aws_region,
        bedrock_endpoint_url=bedrock_endpoint_url,
        bedrock_aws_access_key_id=bedrock_access_key_id,
        bedrock_aws_secret_access_key=bedrock_secret_access_key,
        bedrock_aws_session_token=bedrock_session_token,
        bedrock_aws_bearer_token=bedrock_bearer_token,
        provider_models=new_provider_models,
        ingest_selector_model=ingest_selector_model,
    )
    log.info(
        "admin: %s updated llm settings provider=%s model=%s",
        actor.id,
        provider,
        model,
    )
    return _llm_view(llm_settings.get())


@router.post("/llm/{provider_name}/test", response_model=ProviderTestResult)
def test_llm_provider(
    provider_name: str,
    req: ProviderTestRequest,
    _actor: User = Depends(require_admin),
) -> ProviderTestResult:
    """Preflight the SAVED provider config. Interactive diagnostics —
    bounded by the provider's preflight timeout, so it stays inline rather
    than going through a task queue."""
    provider = llm_providers.get(provider_name)
    if provider is None:
        raise HTTPException(status_code=404, detail=f"unknown provider '{provider_name}'")
    s = llm_settings.get()
    try:
        provider.check_configured(s)
    except LLMError as exc:
        raise HTTPException(status_code=400, detail=exc.message) from exc
    saved_models = s.provider_models.get(provider_name, [])
    model = (
        (req.model or "").strip()
        or (saved_models[0] if saved_models else "")
        or (s.model if s.provider == provider_name else "")
    )
    if not model:
        raise HTTPException(status_code=400, detail="add a model name before testing")
    return ProviderTestResult.model_validate(provider.test_connection(s, model=model))


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
        actor.id,
        bool(serper_key),
        bool(firecrawl_key),
    )
    return _web_view(web_settings.get())


# --------------------------------------------------------------------------- #
# Ingest settings (inbound document push from external systems)
# --------------------------------------------------------------------------- #


_MIN_DOC_CHARS = 1_000
_MAX_DOC_CHARS = 5_000_000


def _ingest_view(s: IngestSettings) -> IngestView:
    updated_by = (
        users_repo.get_by_id(s.updated_by_user_id) if s.updated_by_user_id else None
    )
    return IngestView(
        max_doc_chars=s.max_doc_chars,
        api_key_set=bool(s.api_key),
        api_key_hint=_redact(s.api_key or ""),
        onyx_base_url=s.onyx_base_url,
        warn_update_threshold_default=s.warn_update_threshold_default,
        auto_update_cap=s.auto_update_cap,
        updated_at=s.updated_at,
        updated_by_email=updated_by["email"] if updated_by else None,
    )


@router.get("/ingest", response_model=IngestView)
def get_ingest(_actor: User = Depends(require_admin)) -> IngestView:
    return _ingest_view(ingest_settings.get())


@router.put("/ingest", response_model=IngestView)
def put_ingest(
    req: IngestConfigRequest,
    actor: User = Depends(require_admin),
) -> IngestView:
    if req.max_doc_chars < _MIN_DOC_CHARS or req.max_doc_chars > _MAX_DOC_CHARS:
        raise HTTPException(
            status_code=400,
            detail=f"max_doc_chars must be between {_MIN_DOC_CHARS} and {_MAX_DOC_CHARS}",
        )
    # Omitted (None) preserves the stored URL; an explicit empty string clears
    # it. Otherwise validate and set. Avoids a max_doc_chars-only PUT wiping it.
    if req.onyx_base_url is None:
        onyx_base_url = ingest_settings.get().onyx_base_url
    else:
        onyx_base_url = req.onyx_base_url.strip() or None
        if onyx_base_url is not None:
            try:
                validate_onyx_base_url(onyx_base_url)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
    ingest_settings.upsert(
        max_doc_chars=req.max_doc_chars,
        onyx_base_url=onyx_base_url,
        warn_update_threshold_default=req.warn_update_threshold_default,
        auto_update_cap=req.auto_update_cap,
        updated_by_user_id=actor.id,
    )
    log.info(
        "admin: %s updated ingest settings max_doc_chars=%d onyx_base_url_set=%s "
        "warn_default=%s auto_update_cap=%s",
        actor.id,
        req.max_doc_chars,
        bool(onyx_base_url),
        req.warn_update_threshold_default,
        req.auto_update_cap,
    )
    return _ingest_view(ingest_settings.get())


@router.post("/ingest/regenerate-key", response_model=RegenerateKeyResponse)
def regenerate_ingest_key(
    actor: User = Depends(require_admin),
) -> RegenerateKeyResponse:
    key = ingest_settings.regenerate_key(updated_by_user_id=actor.id)
    log.info("admin: %s regenerated ingest api_key", actor.id)
    return RegenerateKeyResponse(api_key=key)


# --------------------------------------------------------------------------- #
# Braintrust tracing settings                                                 #
# --------------------------------------------------------------------------- #


def _slack_app_view(s: SlackAppSettingsModel) -> SlackAppView:
    secret = s.client_secret.get_secret_value()
    return SlackAppView(
        client_id=s.client_id,
        client_secret_set=bool(secret),
        client_secret_hint=_redact(secret),
    )


@router.get("/slack-app", response_model=SlackAppView)
def get_slack_app(_actor: User = Depends(require_admin)) -> SlackAppView:
    return _slack_app_view(slack_app_settings.get())


@router.put("/slack-app", response_model=SlackAppView)
def put_slack_app(
    req: SlackAppConfigRequest,
    actor: User = Depends(require_admin),
) -> SlackAppView:
    current = slack_app_settings.get()
    # Omitted fields keep their stored value, matching the secret convention.
    if "client_id" in req.model_fields_set:
        client_id = req.client_id.strip()
    else:
        client_id = current.client_id
    if "client_secret" not in req.model_fields_set or req.client_secret == "":
        client_secret = current.client_secret.get_secret_value()
    elif req.client_secret is None:
        client_secret = ""
    else:
        client_secret = req.client_secret
    slack_app_settings.upsert(client_id=client_id, client_secret=client_secret)
    log.info(
        "admin: %s updated slack app settings client_id_set=%s secret_set=%s",
        actor.id, bool(client_id), bool(client_secret),
    )
    return _slack_app_view(slack_app_settings.get())


def _email_smtp_view(s: EmailSmtpSettingsModel) -> EmailSmtpView:
    password = s.password.get_secret_value()
    return EmailSmtpView(
        host=s.host,
        port=s.port,
        username=s.username,
        password_set=bool(password),
        password_hint=_redact(password),
        from_address=s.from_address,
    )


@router.get("/email-smtp", response_model=EmailSmtpView)
def get_email_smtp(_actor: User = Depends(require_admin)) -> EmailSmtpView:
    return _email_smtp_view(email_settings.get())


@router.put("/email-smtp", response_model=EmailSmtpView)
def put_email_smtp(
    req: EmailSmtpConfigRequest,
    actor: User = Depends(require_admin),
) -> EmailSmtpView:
    current = email_settings.get()
    # Omitted fields keep their stored value, matching the secret convention.
    host = req.host.strip() if "host" in req.model_fields_set else current.host
    port = req.port if "port" in req.model_fields_set else current.port
    username = req.username.strip() if "username" in req.model_fields_set else current.username
    from_address = (
        req.from_address.strip()
        if "from_address" in req.model_fields_set
        else current.from_address
    )
    if "password" not in req.model_fields_set or req.password == "":
        password = current.password.get_secret_value()
    elif req.password is None:
        password = ""
    else:
        password = req.password
    email_settings.upsert(
        host=host, port=port, username=username, password=password, from_address=from_address
    )
    log.info(
        "admin: %s updated smtp settings host_set=%s from_set=%s password_set=%s",
        actor.id, bool(host), bool(from_address), bool(password),
    )
    return _email_smtp_view(email_settings.get())


@router.post("/email-smtp/test", response_model=EmailTestResponse)
def post_email_smtp_test(
    req: EmailTestRequest,
    actor: User = Depends(require_admin),
) -> EmailTestResponse:
    """Synchronous end-to-end send so an admin can prove the account works
    before any consumer exists."""
    to = req.to.strip() or actor.email
    try:
        email_service.send(
            to=to,
            subject="Agent Wiki test email",
            text="This is a test message from your Agent Wiki SMTP configuration. "
            "If you are reading it, outbound email works.",
        )
    except EmailSendError as e:
        return EmailTestResponse(ok=False, detail=f"send failed: {e}")
    return EmailTestResponse(ok=True, detail=f"sent to {to}")


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
        actor.id,
        project,
        bool(api_key),
        enabled,
    )
    return _braintrust_view(braintrust_settings.get())
