"""HTTP shapes for /api/documents."""
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

    content: str = Field(min_length=1)
    title: str | None = None
    source_type: str | None = None
    metadata: dict[str, Any] | None = None
    updated_at: str | None = None
    diff: str | None = None


# --------------------------------------------------------------------------- #
# Responses                                                                   #
# --------------------------------------------------------------------------- #


class ListDocumentsResponse(BaseModel):
    paths: list[str]


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


class FileHistoryResponse(BaseModel):
    path: str
    head_sha: str | None
    commits: list[CommitView]
