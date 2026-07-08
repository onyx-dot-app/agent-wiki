"""HTTP shapes for /api/triggers."""
from __future__ import annotations

from pydantic import BaseModel, Field


class TriggerActionShape(BaseModel):
    """One authored delivery action: the message to render on fire and the
    destination config it goes to (null = event-log only)."""

    destination_config_id: str | None = None
    message: str = Field(min_length=1)


class TriggerActionView(BaseModel):
    """Read-side action. Tolerates a null message so historical YAML versions
    render instead of failing validation."""

    destination_config_id: str | None = None
    message: str | None = None


# --------------------------------------------------------------------------- #
# Requests                                                                    #
# --------------------------------------------------------------------------- #


class TriggerScopeShape(BaseModel):
    """One watch entry: a page or folder path, optionally narrowed to a
    1-based inclusive line range (pages only)."""

    path: str = Field(min_length=1)
    start_line: int | None = Field(default=None, ge=1)
    end_line: int | None = Field(default=None, ge=1)


class CreateTriggerRequest(BaseModel):
    """``actions`` is the delivery list; each entry's ``destination_config_id``
    must reference a destination config the caller owns (GET
    /triggers/destination-configs) or be null for event-log-only fires.
    Ownership is validated in the repo.

    For ``kind="schedule"`` triggers, ``schedule_cron`` and
    ``schedule_timezone`` are required and ``schedule_start_at`` is
    optional. Schedule-field validation also lives in the repo.
    """

    scope_path: str = Field(min_length=1)
    # Full watch list; when present, scopes[0].path governs and scope_path
    # may be omitted from consideration (kept for older clients).
    scopes: list[TriggerScopeShape] | None = None
    nl_description: str = Field(min_length=1)
    actions: list[TriggerActionShape] = Field(min_length=1)
    kind: str = "delta"
    enabled: bool = True
    schedule_cron: str | None = None
    schedule_timezone: str | None = None
    schedule_start_at: str | None = None


class UpdateTriggerRequest(BaseModel):
    """All fields optional — the route only updates the ones that were sent."""

    scope_path: str | None = None
    scopes: list[TriggerScopeShape] | None = None
    nl_description: str | None = None
    actions: list[TriggerActionShape] | None = None
    enabled: bool | None = None
    schedule_cron: str | None = None
    schedule_timezone: str | None = None
    schedule_start_at: str | None = None


# --------------------------------------------------------------------------- #
# Responses                                                                   #
# --------------------------------------------------------------------------- #


class TriggerView(BaseModel):
    """API view of a trigger row. ``actions`` comes from
    ``Trigger.action_json``. Schedule fields are only populated for
    ``kind="schedule"`` triggers."""
    # Transient, set by create/update when the scope doesn't exist yet.
    scope_warning: str | None = None

    id: str
    owner_user_id: str
    scope_path: str
    scopes: list[TriggerScopeShape] = Field(default_factory=list)
    kind: str
    nl_description: str
    actions: list[TriggerActionView]
    enabled: bool
    created_at: str | None
    last_edited_at: str | None
    file_path: str | None
    schedule_cron: str | None = None
    schedule_timezone: str | None = None
    schedule_start_at: str | None = None
    schedule_last_fired_at: str | None = None


class TriggerListResponse(BaseModel):
    triggers: list[TriggerView]


class TriggerFireView(BaseModel):
    """One recorded ``trigger.fire`` event, flattened from the events row."""

    event_id: int
    trigger_id: str
    ts: str
    doc_path: str
    change_kind: str
    reason: str
    message: str
    destination_type: str
    destination_config_id: str | None


class TriggerFiresResponse(BaseModel):
    fires: list[TriggerFireView]


class TriggerCommit(BaseModel):
    sha: str
    author: str
    ts: str
    message: str
    body: str


class TriggerHistoryResponse(BaseModel):
    commits: list[TriggerCommit]


class TriggerDestinationView(BaseModel):
    """One row in the ``trigger_destinations`` catalog."""

    id: str
    name: str
    description: str


class TriggerDestinationsResponse(BaseModel):
    destinations: list[TriggerDestinationView]


class TriggerVersionResponse(BaseModel):
    """A trigger's fields as they existed at a specific commit."""

    scope_path: str | None
    nl_description: str | None
    actions: list[TriggerActionView]
    enabled: bool
    sha: str
    path: str
    kind: str | None = None
    schedule_cron: str | None = None
    schedule_timezone: str | None = None
    schedule_start_at: str | None = None
