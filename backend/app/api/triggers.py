"""FastAPI port of ``app/api/triggers.py`` (Phase 3)."""
from __future__ import annotations

import logging
import re
from typing import Any, cast

from fastapi import APIRouter, Depends, HTTPException, Response, status

from app.auth import User, require_can
from app.auth.deps import require_user
from app.models.destination_config import (
    CreateDestinationConfigRequest,
    DestinationConfigListResponse,
    DestinationConfigView,
)
from app.models.trigger import (
    CreateTriggerRequest,
    TriggerActionView,
    TriggerCommit,
    TriggerDestinationsResponse,
    TriggerDestinationView,
    TriggerHistoryResponse,
    TriggerListResponse,
    TriggerVersionResponse,
    TriggerView,
    UpdateTriggerRequest,
)
from app.email.service import EmailSendError
from app.triggers import destination_configs as dest_configs
from app.triggers import email_verification
from app.triggers import destinations as destinations_repo
from app.triggers import repo as triggers_repo
from app.triggers import storage as triggers_storage
from app.wiki import git as wiki_git

router = APIRouter()
log = logging.getLogger(__name__)

_SHA_RE = re.compile(r"^[0-9a-f]{4,40}$")


def _git_author(user: User) -> str:
    return f"{user.name or user.email} <{user.email}>"


def _config_view(row: dict[str, Any]) -> DestinationConfigView:
    return DestinationConfigView(
        id=row["id"],
        type=row["type"],
        name=row["name"],
        config=cast("dict[str, Any]", row.get("config") or {}),
        has_secret=bool(row.get("has_secret")),
        verified_at=row.get("verified_at"),
        created_at=row.get("created_at"),
    )


def _normalize_scope_path(raw: str, user: User) -> str:
    if not raw.strip():
        raise ValueError("scope_path is required")
    return triggers_storage.normalize_scope_path(raw, reader=user)


def _to_view(row: dict[str, Any]) -> TriggerView:
    return TriggerView.model_validate(row)


@router.get("", response_model=TriggerListResponse)
def list_triggers(user: User = Depends(require_user)) -> TriggerListResponse:
    rows = triggers_repo.list_for_owner(user.id)
    return TriggerListResponse(triggers=[_to_view(r) for r in rows])


@router.get("/destinations", response_model=TriggerDestinationsResponse)
def list_destinations(
    _user: User = Depends(require_user),
) -> TriggerDestinationsResponse:
    """Catalog of where a trigger fire can be delivered. Global,
    login-only — no per-user filter."""
    rows = destinations_repo.list_all()
    return TriggerDestinationsResponse(
        destinations=[
            TriggerDestinationView(
                id=r["id"], name=r["name"], description=r["description"]
            )
            for r in rows
        ],
    )


@router.get("/destination-configs", response_model=DestinationConfigListResponse)
def list_destination_configs(
    user: User = Depends(require_user),
) -> DestinationConfigListResponse:
    """The caller's own destination configs. Secrets are never returned."""
    rows = dest_configs.list_for_user(user.id)
    return DestinationConfigListResponse(configs=[_config_view(r) for r in rows])


@router.post(
    "/destination-configs",
    response_model=DestinationConfigView,
    status_code=status.HTTP_201_CREATED,
)
def create_destination_config(
    req: CreateDestinationConfigRequest, user: User = Depends(require_user)
) -> DestinationConfigView:
    try:
        row = dest_configs.create(
            user.id, type=req.type, name=req.name, config=req.config, secret=req.secret
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    view = _config_view(row)
    # Idempotent create can return an existing row; a verified address needs
    # no fresh link.
    if req.type == destinations_repo.EMAIL_ID and not row.get("verified_at"):
        view.verification_error = _send_verify_link(row)
    return view


def _send_verify_link(row: dict[str, Any]) -> str | None:
    """Fire the verify email for an email config; a failure is reported on
    the view, not raised — the config itself was created."""
    address = str(cast("dict[str, Any]", row.get("config") or {}).get("address") or "")
    try:
        email_verification.send_verification_email(str(row["id"]), address)
    except email_verification.MintRateLimitedError as exc:
        return str(exc)
    except EmailSendError as exc:
        return f"verification email failed: {exc}"
    return None


@router.post(
    "/destination-configs/{config_id}/resend-verify",
    response_model=DestinationConfigView,
)
def resend_verification(
    config_id: str, user: User = Depends(require_user)
) -> DestinationConfigView:
    row = dest_configs.get(config_id, user.id)
    if row is None:
        raise HTTPException(status_code=404, detail="not found")
    if row.get("type") != destinations_repo.EMAIL_ID:
        raise HTTPException(status_code=400, detail="not an email destination")
    if row.get("verified_at"):
        raise HTTPException(status_code=400, detail="already verified")
    address = str(cast("dict[str, Any]", row.get("config") or {}).get("address") or "")
    view = _config_view(row)
    try:
        email_verification.send_verification_email(config_id, address)
    except email_verification.MintRateLimitedError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except EmailSendError as exc:
        view.verification_error = f"verification email failed: {exc}"
    return view


@router.delete(
    "/destination-configs/{config_id}", status_code=status.HTTP_204_NO_CONTENT
)
def delete_destination_config(
    config_id: str, user: User = Depends(require_user)
) -> Response:
    if not dest_configs.delete(config_id, user.id):
        raise HTTPException(status_code=404, detail="not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "", response_model=TriggerView, status_code=status.HTTP_201_CREATED
)
def create_trigger(
    req: CreateTriggerRequest, user: User = Depends(require_user),
) -> TriggerView:
    try:
        scope_path = _normalize_scope_path(req.scope_path, user)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # A trigger reads the scope at fire-time to render its message; require
    # the same up-front so users can't watch paths they can't see.
    require_can("read", scope_path, user)

    if req.kind not in triggers_repo.ALLOWED_KINDS:
        raise HTTPException(status_code=400, detail=f"unsupported kind: {req.kind!r}")

    try:
        trigger = triggers_repo.create(
            owner_user_id=user.id,
            scope_path=scope_path,
            nl_description=req.nl_description.strip(),
            actions=[a.model_dump() for a in req.actions],
            kind=req.kind,
            enabled=req.enabled,
            actor=_git_author(user),
            schedule_cron=req.schedule_cron,
            schedule_timezone=req.schedule_timezone,
            schedule_start_at=req.schedule_start_at,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    view = _to_view(trigger)
    if not triggers_storage.scope_exists(scope_path):
        view.scope_warning = (
            f"{scope_path!r} does not exist yet; the trigger fires when it is created"
        )
    log.info(
        "trigger created id=%s owner=%s scope=%s kind=%s enabled=%s",
        trigger.get("id"), user.id, scope_path, req.kind, req.enabled,
    )
    return view


@router.put("/{trigger_id}", response_model=TriggerView)
def update_trigger(
    trigger_id: str,
    req: UpdateTriggerRequest,
    user: User = Depends(require_user),
) -> TriggerView:
    existing = triggers_repo.get(trigger_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="not found")
    if existing["owner_user_id"] != user.id:
        raise HTTPException(status_code=403, detail="forbidden")

    # ``model_fields_set`` lets us treat fields the client *sent* —
    # including explicit ``null`` clears for ``schedule_start_at`` —
    # differently from fields it omitted (left untouched).
    sent_fields = req.model_fields_set
    kwargs: dict[str, Any] = {}

    if "scope_path" in sent_fields:
        try:
            kwargs["scope_path"] = _normalize_scope_path(req.scope_path or "", user)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    # Require read access against whichever scope ends up sticking — the
    # new one if rebinding, otherwise the existing one (in case ACLs
    # were revoked after the trigger was created).
    final_scope = kwargs.get("scope_path", existing["scope_path"])
    require_can("read", final_scope, user)

    if "nl_description" in sent_fields:
        nl = (req.nl_description or "").strip()
        if not nl:
            raise HTTPException(status_code=400, detail="nl_description cannot be empty")
        kwargs["nl_description"] = nl

    if "actions" in sent_fields:
        kwargs["actions"] = [a.model_dump() for a in (req.actions or [])]

    if "enabled" in sent_fields:
        kwargs["enabled"] = req.enabled

    if "schedule_cron" in sent_fields:
        kwargs["schedule_cron"] = req.schedule_cron

    if "schedule_timezone" in sent_fields:
        kwargs["schedule_timezone"] = req.schedule_timezone

    if "schedule_start_at" in sent_fields:
        # Pass through ``None`` so the user can clear the anchor.
        kwargs["schedule_start_at"] = req.schedule_start_at

    try:
        updated = triggers_repo.update(trigger_id, actor=_git_author(user), **kwargs)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if updated is None:
        raise HTTPException(status_code=404, detail="not found")
    log.info(
        "trigger updated id=%s owner=%s fields=%s",
        trigger_id, user.id, sorted(kwargs.keys()),
    )
    view = _to_view(updated)
    if "scope_path" in kwargs and not triggers_storage.scope_exists(kwargs["scope_path"]):
        view.scope_warning = (
            f"{kwargs['scope_path']!r} does not exist yet; the trigger fires when it is created"
        )
    return view


@router.delete("/{trigger_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_trigger(
    trigger_id: str, user: User = Depends(require_user),
) -> Response:
    existing = triggers_repo.get(trigger_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="not found")
    if existing["owner_user_id"] != user.id:
        raise HTTPException(status_code=403, detail="forbidden")
    triggers_repo.delete(trigger_id, actor=_git_author(user))
    log.info("trigger deleted id=%s owner=%s", trigger_id, user.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{trigger_id}/history", response_model=TriggerHistoryResponse)
def trigger_history(
    trigger_id: str, user: User = Depends(require_user),
) -> TriggerHistoryResponse:
    """Git history for the trigger's YAML file (config edits, not fires).

    Fire history lives in the events table — see ``GET /api/events``.
    """
    existing = triggers_repo.get(trigger_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="not found")
    if existing["owner_user_id"] != user.id:
        raise HTTPException(status_code=403, detail="forbidden")
    file_path = existing.get("file_path")
    if not file_path:
        return TriggerHistoryResponse(commits=[])
    commits = [TriggerCommit(**c.model_dump()) for c in wiki_git.history(file_path)]
    return TriggerHistoryResponse(commits=commits)


@router.get(
    "/{trigger_id}/version/{sha}", response_model=TriggerVersionResponse
)
def trigger_version(
    trigger_id: str, sha: str, user: User = Depends(require_user),
) -> TriggerVersionResponse:
    """Read the trigger's fields as they existed at a specific commit."""
    if not _SHA_RE.match(sha):
        raise HTTPException(status_code=400, detail="invalid sha")
    existing = triggers_repo.get(trigger_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="not found")
    if existing["owner_user_id"] != user.id:
        raise HTTPException(status_code=403, detail="forbidden")

    path = triggers_storage.find_path_at_sha(trigger_id, sha)
    if not path:
        raise HTTPException(status_code=404, detail="trigger not present at that revision")
    try:
        data = triggers_storage.read_trigger_at(path, sha)
    except Exception as exc:
        log.exception("failed to read trigger %s at %s", trigger_id, sha)
        raise HTTPException(status_code=500, detail="failed to read version") from exc

    return TriggerVersionResponse(
        scope_path=data.get("scope_path"),
        nl_description=data.get("nl_description"),
        actions=[
            TriggerActionView.model_validate(a)
            for a in cast(list[dict[str, Any]], data.get("actions") or [])
        ],
        enabled=bool(data.get("enabled", True)),
        sha=sha,
        path=path,
        kind=data.get("kind"),
        schedule_cron=data.get("schedule_cron"),
        schedule_timezone=data.get("schedule_timezone"),
        schedule_start_at=data.get("schedule_start_at"),
    )
