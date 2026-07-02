"""HTTP shapes for the per-user destination config registry.

Backs the ``/triggers/destination-configs`` CRUD surface. ``secret`` (e.g. a
Slack incoming webhook URL) is accepted on create but never returned.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class CreateDestinationConfigRequest(BaseModel):
    """A named, typed delivery target the caller owns. ``type`` is a
    ``trigger_destinations`` slug. ``secret`` (e.g. the Slack webhook URL) is
    stored encrypted and never returned."""

    type: str = Field(min_length=1)
    name: str = Field(min_length=1, max_length=80)
    secret: str | None = None
    config: dict[str, Any] = Field(default_factory=dict)


class DestinationConfigView(BaseModel):
    """A destination config in list form. The secret is never returned, only
    whether one is set. ``config`` is the non-secret per-type settings (e.g.
    a slack channel reference), which the UI needs for dedup and display."""

    id: str
    type: str
    name: str
    config: dict[str, Any] = Field(default_factory=dict)
    has_secret: bool
    created_at: str | None


class DestinationConfigListResponse(BaseModel):
    configs: list[DestinationConfigView]
