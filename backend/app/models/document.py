from __future__ import annotations

from pydantic import BaseModel


class Document(BaseModel):
    id: str
    path: str
    title: str | None = None
    updated_at: str


class DocumentUpdate(BaseModel):
    body: str
    message: str  # commit message


class IngestPayload(BaseModel):
    source: str
    payload: dict
    target_path: str | None = None  # optional hint to the agent
