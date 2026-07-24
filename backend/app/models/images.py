"""HTTP shapes for /api/wiki/images."""

from __future__ import annotations

from pydantic import BaseModel


class UploadImageResponse(BaseModel):
    id: str
    url: str
    markdown: str
