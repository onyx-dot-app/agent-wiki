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
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _summary(row: dict[str, Any]) -> DocumentTemplateSummary:
    return DocumentTemplateSummary(
        id=row["id"],
        name=row["name"],
        description=row["description"],
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
        row = templates_repo.update(
            template_id,
            name=name,
            body=req.body,
            description=(req.description or None),
            system_prompt=(req.system_prompt or None),
        )
    except templates_repo.TemplateNameTaken as exc:
        raise HTTPException(
            status_code=409, detail="a template with that name already exists",
        ) from exc
    if row is None:
        raise HTTPException(status_code=404, detail="not found")
    log.info("admin: %s updated template %s", actor.id, template_id)
    return _view(row)


@admin_router.delete("/{template_id}", response_model=OkResponse)
def delete_template(
    template_id: str, actor: User = Depends(require_admin),
) -> OkResponse:
    if not templates_repo.delete(template_id):
        raise HTTPException(status_code=404, detail="not found")
    log.info("admin: %s deleted template %s", actor.id, template_id)
    return OkResponse()
