"""HTTP shapes for /api/triggers."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


# --------------------------------------------------------------------------- #
# Forward-compatibility schemas (kept for the typed surface)                  #
# --------------------------------------------------------------------------- #


class TriggerAction(BaseModel):
    kind: Literal["webhook", "http", "agent_message"]
    config: dict[str, Any]


# --------------------------------------------------------------------------- #
# Requests                                                                    #
# --------------------------------------------------------------------------- #


class CreateTriggerRequest(BaseModel):
    """v0 only honors ``kind=delta`` and ``destination=null``; the values are
    still validated in the route against the repo's allow-lists for clearer
    error messages."""

    scope_path: str = Field(min_length=1)
    nl_description: str = Field(min_length=1)
    message: str = Field(min_length=1)
    destination: str | None = None
    kind: str = "delta"
    enabled: bool = True


class UpdateTriggerRequest(BaseModel):
    """All fields optional — the route only updates the ones that were sent."""

    scope_path: str | None = None
    nl_description: str | None = None
    message: str | None = None
    destination: str | None = None
    enabled: bool | None = None


# --------------------------------------------------------------------------- #
# Responses                                                                   #
# --------------------------------------------------------------------------- #


class TriggerView(BaseModel):
    """API view of a trigger row. ``message`` and ``destination`` are
    flattened from ``Trigger.action_json``."""

    id: str
    owner_user_id: str
    scope_path: str
    kind: str
    nl_description: str
    message: str | None
    destination: str | None
    enabled: bool
    created_at: str | None
    last_edited_at: str | None
    file_path: str | None


class TriggerListResponse(BaseModel):
    triggers: list[TriggerView]


class TriggerCommit(BaseModel):
    sha: str
    author: str
    ts: str
    message: str
    body: str


class TriggerHistoryResponse(BaseModel):
    commits: list[TriggerCommit]


class TriggerVersionResponse(BaseModel):
    """A trigger's fields as they existed at a specific commit."""

    scope_path: str | None
    nl_description: str | None
    message: str | None
    destination: str | None
    enabled: bool
    sha: str
    path: str
