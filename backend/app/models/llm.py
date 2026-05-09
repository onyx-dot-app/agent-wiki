"""HTTP-shape models for the non-admin LLM endpoints."""
from __future__ import annotations

from pydantic import BaseModel


class LLMStatusResponse(BaseModel):
    """Whether the system has a usable LLM configured. Safe for any
    logged-in user — no keys, no model names beyond the provider id."""

    configured: bool
    provider: str
