"""HTTP shapes for /api/wiki."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


# --------------------------------------------------------------------------- #
# Existing schemas (kept for forward compatibility)                           #
# --------------------------------------------------------------------------- #


class Document(BaseModel):
    id: str
    path: str
    title: str | None = None
    updated_at: str


class DocumentUpdate(BaseModel):
    body: str
    message: str  # commit message


# --------------------------------------------------------------------------- #
# Requests                                                                    #
# --------------------------------------------------------------------------- #


class PutDocumentRequest(BaseModel):
    path: str = Field(min_length=1)
    body: str = ""
    base_sha: str | None = None


class CreateFolderRequest(BaseModel):
    path: str = Field(min_length=1)


class MovePathRequest(BaseModel):
    old_path: str = Field(min_length=1)
    new_path: str = Field(min_length=1)


class ReindexRequest(BaseModel):
    path: str = Field(min_length=1)


class IngestRequest(BaseModel):
    """Inbound document push from external systems (e.g. Onyx connectors)."""

    model_config = {"populate_by_name": True}

    content: str = Field(min_length=1)
    title: str | None = None
    source: str | None = None
    source_document_id: str | None = Field(default=None, alias="document_id")
    url: str | None = None
    metadata: dict[str, Any] | None = None
    updated_at: str | None = None
    diff: str | None = None


# --------------------------------------------------------------------------- #
# Responses                                                                   #
# --------------------------------------------------------------------------- #


class DocumentEntry(BaseModel):
    path: str
    updated_at: str  # ISO-8601 author-time of the most recent commit touching the path


class ListDocumentsResponse(BaseModel):
    entries: list[DocumentEntry]


class GetDocumentResponse(BaseModel):
    path: str
    body: str
    head_sha: str | None
    ref: str | None = None  # only set when reading at a specific ref


class PutDocumentResponse(BaseModel):
    path: str
    sha: str
    created: bool
    deprecated: list[str]


class CreateFolderResponse(BaseModel):
    path: str
    sha: str


class MovedFile(BaseModel):
    old: str
    new: str


class MovePathResponse(BaseModel):
    old_path: str
    new_path: str
    sha: str
    moved: list[MovedFile]


class DeleteDocumentResponse(BaseModel):
    sha: str


class ReindexResponse(BaseModel):
    path: str
    queued: bool


class IngestResponse(BaseModel):
    queued: bool
    task_id: str | None


class IngestTooLargeResponse(BaseModel):
    """413 response when an ingest payload exceeds ``max_doc_chars``."""

    error: str
    limit: int
    received: int


class CommitView(BaseModel):
    sha: str
    author: str
    ts: str
    message: str
    body: str = ""


class FileHistoryResponse(BaseModel):
    path: str
    head_sha: str | None
    commits: list[CommitView]


class SearchHitView(BaseModel):
    doc_id: str
    path: str
    title: str | None
    snippet: str
    score: float


class FolderHitView(BaseModel):
    path: str


class SearchResponse(BaseModel):
    query: str
    hits: list[SearchHitView]
    folders: list[FolderHitView] = []


# --------------------------------------------------------------------------- #
# Agent activity                                                              #
# --------------------------------------------------------------------------- #


class ActivityRowView(BaseModel):
    """One active registration on a doc — what the UI / agents see.

    Mirror of ``app.wiki.agent_activity.ActivityRow`` minus the internal
    ``id`` and ``user_id``. ``owner_display`` is the user's display
    name (falling back to email); ``agent_name`` is ``None`` when the
    agent didn't identify itself.
    """

    owner_display: str
    agent_name: str | None
    activity: str           # "read" | "wrote"
    description: str | None
    registered_at: str
    expires_at: str
    agent_session_id: str | None


class DocumentActivityResponse(BaseModel):
    path: str
    agents: list[ActivityRowView]


# --------------------------------------------------------------------------- #
# Document drafting (template-seeded pages)                                   #
# --------------------------------------------------------------------------- #


class DocumentDraftView(BaseModel):
    """Active "drafting from template" state for a wiki page."""

    path: str
    template_id: str
    template_name: str | None
    system_prompt: str | None
    created_at: str


class SetDocumentDraftRequest(BaseModel):
    """Body for ``POST /api/wiki/file/draft``.

    ``template_id=None`` clears the draft row. Otherwise upserts: the
    template's current body becomes the divergence snapshot.
    """

    path: str = Field(min_length=1)
    template_id: str | None = None


# --------------------------------------------------------------------------- #
# Human drafts                                                                #
# --------------------------------------------------------------------------- #


class DraftRequest(BaseModel):
    """Body for ``PUT /api/wiki/file/autosave`` (auto-save from the editor)."""

    path: str = Field(min_length=1)
    base_sha: str
    content: str


class DraftResponse(BaseModel):
    path: str
    base_sha: str
    content: str
    updated_at: str


class RebaseRequest(BaseModel):
    """Body for ``POST /api/wiki/file/autosave/rebase``."""

    path: str = Field(min_length=1)


class RebaseConflictResponse(BaseModel):
    """409 body from ``POST /api/wiki/file/autosave/rebase`` when merge has conflicts."""

    error: str
    current_body: str
    draft_body: str
    current_sha: str
