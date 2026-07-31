"""HTTP shapes for /api/wiki/media."""

from __future__ import annotations

from pydantic import BaseModel


class UploadMediaResponse(BaseModel):
    id: str
    url: str
    markdown: str
