"""Document templates — admin CRUD plus a read-only summary list for
authenticated users (so the new-doc picker can show available templates
without exposing the full body/system_prompt to non-admins until selected).
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status

from app.auth import User
from app.auth.deps import require_admin, require_user
from app.models.admin import OkResponse
from app.models.template import (
    CreateDocumentTemplateRequest,
    DocumentTemplateListResponse,
    DocumentTemplateSummary,
    DocumentTemplateSummaryListResponse,
    DocumentTemplateView,
    ReorderDocumentTemplatesRequest,
    UpdateDocumentTemplateRequest,
)
from app.wiki import templates as templates_repo

router = APIRouter()
admin_router = APIRouter()
log = logging.getLogger(__name__)


def _view(row: dict[str, Any]) -> DocumentTemplateView:
    return DocumentTemplateView(
        id=row["id"],
        name=row["name"],
        body=row["body"],
        description=row["description"],
        system_prompt=row["system_prompt"],
        ingestion_auto_update_disabled=row["ingestion_auto_update_disabled"],
        update_instruction=row["update_instruction"],
        sort_order=row["sort_order"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _summary(row: dict[str, Any]) -> DocumentTemplateSummary:
    return DocumentTemplateSummary(
        id=row["id"],
        name=row["name"],
        description=row["description"],
        ingestion_auto_update_disabled=row["ingestion_auto_update_disabled"],
    )


# --------------------------------------------------------------------------- #
# Public (authed) endpoints                                                   #
# --------------------------------------------------------------------------- #


@router.get("", response_model=DocumentTemplateSummaryListResponse)
def list_for_picker(
    _user: User = Depends(require_user),
) -> DocumentTemplateSummaryListResponse:
    return DocumentTemplateSummaryListResponse(
        templates=[_summary(r) for r in templates_repo.list_all()],
    )


@router.get("/{template_id}", response_model=DocumentTemplateView)
def get_template(
    template_id: str, _user: User = Depends(require_user),
) -> DocumentTemplateView:
    row = templates_repo.get(template_id)
    if row is None:
        raise HTTPException(status_code=404, detail="not found")
    return _view(row)


# --------------------------------------------------------------------------- #
# Admin endpoints                                                             #
# --------------------------------------------------------------------------- #


@admin_router.get("", response_model=DocumentTemplateListResponse)
def list_all_admin(
    _actor: User = Depends(require_admin),
) -> DocumentTemplateListResponse:
    return DocumentTemplateListResponse(
        templates=[_view(r) for r in templates_repo.list_all()],
    )


@admin_router.post(
    "", response_model=DocumentTemplateView, status_code=status.HTTP_201_CREATED,
)
def create_template(
    req: CreateDocumentTemplateRequest,
    actor: User = Depends(require_admin),
) -> DocumentTemplateView:
    name = req.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="name is required")
    if not req.body:
        raise HTTPException(status_code=400, detail="body is required")
    try:
        row = templates_repo.create(
            name=name,
            body=req.body,
            description=(req.description or None),
            system_prompt=(req.system_prompt or None),
            ingestion_auto_update_disabled=req.ingestion_auto_update_disabled,
            update_instruction=(req.update_instruction or None),
            created_by_user_id=actor.id,
        )
    except templates_repo.TemplateNameTaken as exc:
        raise HTTPException(
            status_code=409, detail="a template with that name already exists",
        ) from exc
    log.info("admin: %s created template %s (%s)", actor.id, row["id"], name)
    return _view(row)


@admin_router.put("/{template_id}", response_model=DocumentTemplateView)
def update_template(
    template_id: str,
    req: UpdateDocumentTemplateRequest,
    actor: User = Depends(require_admin),
) -> DocumentTemplateView:
    name = req.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="name is required")
    if not req.body:
        raise HTTPException(status_code=400, detail="body is required")
    try:
        # Patch only the policy fields actually present in the body; omitted
        # fields keep their stored value (``model_fields_set`` distinguishes
        # "omitted" from an explicit ``null`` clear), so a client that doesn't
        # send them can't silently wipe a template's policy.
        policy: dict[str, Any] = {}
        if "ingestion_auto_update_disabled" in req.model_fields_set:
            policy["ingestion_auto_update_disabled"] = req.ingestion_auto_update_disabled
        if "update_instruction" in req.model_fields_set:
            policy["update_instruction"] = req.update_instruction or None
        row = templates_repo.update(
            template_id,
            name=name,
            body=req.body,
            description=(req.description or None),
            system_prompt=(req.system_prompt or None),
            **policy,
        )
    except templates_repo.TemplateNameTaken as exc:
        raise HTTPException(
            status_code=409, detail="a template with that name already exists",
        ) from exc
    except templates_repo.ProtectedTemplateError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if row is None:
        raise HTTPException(status_code=404, detail="not found")
    log.info("admin: %s updated template %s", actor.id, template_id)
    return _view(row)


@admin_router.delete("/{template_id}", response_model=OkResponse)
def delete_template(
    template_id: str, actor: User = Depends(require_admin),
) -> OkResponse:
    try:
        deleted = templates_repo.delete(template_id)
    except templates_repo.ProtectedTemplateError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if not deleted:
        raise HTTPException(status_code=404, detail="not found")
    log.info("admin: %s deleted template %s", actor.id, template_id)
    return OkResponse()


@admin_router.post("/reorder", response_model=DocumentTemplateListResponse)
def reorder_templates(
    req: ReorderDocumentTemplatesRequest,
    actor: User = Depends(require_admin),
) -> DocumentTemplateListResponse:
    """Set the picker order. Body lists every current template id once,
    in the desired order; the server sets ``sort_order`` to that index."""
    try:
        templates_repo.reorder(req.template_ids)
    except templates_repo.ReorderMismatch as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    log.info("admin: %s reordered %d templates", actor.id, len(req.template_ids))
    return DocumentTemplateListResponse(
        templates=[_view(r) for r in templates_repo.list_all()],
    )
