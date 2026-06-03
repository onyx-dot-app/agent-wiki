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
    """``destination`` is a slug from the ``trigger_destinations`` table
    (default ``"event_log"``). Validation against the destinations
    catalog happens in the repo for a single source of truth.

    For ``kind="schedule"`` triggers, ``schedule_cron`` and
    ``schedule_timezone`` are required and ``schedule_start_at`` is
    optional. Schedule-field validation also lives in the repo.
    """

    scope_path: str = Field(min_length=1)
    nl_description: str = Field(min_length=1)
    message: str = Field(min_length=1)
    destination: str | None = None
    # Required when ``destination == "slack"``: the id of one of the caller's
    # Slack channels (see GET /triggers/slack-webhooks). Ignored otherwise.
    slack_webhook_id: str | None = None
    kind: str = "delta"
    enabled: bool = True
    schedule_cron: str | None = None
    schedule_timezone: str | None = None
    schedule_start_at: str | None = None


class UpdateTriggerRequest(BaseModel):
    """All fields optional — the route only updates the ones that were sent."""

    scope_path: str | None = None
    nl_description: str | None = None
    message: str | None = None
    destination: str | None = None
    slack_webhook_id: str | None = None
    enabled: bool | None = None
    schedule_cron: str | None = None
    schedule_timezone: str | None = None
    schedule_start_at: str | None = None


# --------------------------------------------------------------------------- #
# Responses                                                                   #
# --------------------------------------------------------------------------- #


class TriggerView(BaseModel):
    """API view of a trigger row. ``message`` and ``destination`` are
    flattened from ``Trigger.action_json``. Schedule fields are only
    populated for ``kind="schedule"`` triggers."""

    id: str
    owner_user_id: str
    scope_path: str
    kind: str
    nl_description: str
    message: str | None
    destination: str
    slack_webhook_id: str | None = None
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


class CreateSlackWebhookRequest(BaseModel):
    """A named Slack incoming webhook the caller owns. ``webhook_url`` is the
    secret ``https://hooks.slack.com/…`` URL; it's stored but never returned
    in full (only a masked hint)."""

    name: str = Field(min_length=1, max_length=80)
    webhook_url: str = Field(min_length=1)


class SlackWebhookView(BaseModel):
    """A user's Slack channel in list form — the URL is masked to a hint."""

    id: str
    name: str
    webhook_url_hint: str
    created_at: str | None


class SlackWebhookListResponse(BaseModel):
    webhooks: list[SlackWebhookView]


class TriggerVersionResponse(BaseModel):
    """A trigger's fields as they existed at a specific commit."""

    scope_path: str | None
    nl_description: str | None
    message: str | None
    destination: str | None
    enabled: bool
    sha: str
    path: str
    kind: str | None = None
    schedule_cron: str | None = None
    schedule_timezone: str | None = None
    schedule_start_at: str | None = None
