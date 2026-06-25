"""HTTP shapes for /api/templates and /api/admin/templates."""
from __future__ import annotations

from pydantic import BaseModel, Field


class DocumentTemplateView(BaseModel):
    """Full admin-facing view of a document template."""

    id: str
    name: str
    body: str
    description: str | None
    system_prompt: str | None
    # Default update policy applied to a page created from this template.
    ingestion_auto_update_disabled: bool | None = None
    update_instruction: str | None = None
    sort_order: int
    created_at: str
    updated_at: str


class DocumentTemplateSummary(BaseModel):
    """Public summary surfaced in the new-doc picker. Excludes ``body``
    and ``system_prompt`` so the list endpoint stays cheap; clients fetch
    the full template via ``/api/templates/{id}`` on selection."""

    id: str
    name: str
    description: str | None
    # Surfaced so the picker can flag e.g. "auto-update off" before selection.
    ingestion_auto_update_disabled: bool | None = None


class DocumentTemplateListResponse(BaseModel):
    templates: list[DocumentTemplateView]


class DocumentTemplateSummaryListResponse(BaseModel):
    templates: list[DocumentTemplateSummary]


class CreateDocumentTemplateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1)
    description: str | None = None
    system_prompt: str | None = None
    ingestion_auto_update_disabled: bool | None = None
    update_instruction: str | None = None


class UpdateDocumentTemplateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    body: str = Field(min_length=1)
    description: str | None = None
    system_prompt: str | None = None
    ingestion_auto_update_disabled: bool | None = None
    update_instruction: str | None = None


class ReorderDocumentTemplatesRequest(BaseModel):
    """Full ordered list of every template id — the new order in which
    they should appear in the picker. Partial lists are rejected."""

    template_ids: list[str] = Field(min_length=1)
