"""Structured event payload for workflow (webhook) destinations.

Notification destinations send the rendered prose message. Workflow
destinations send this instead: machine-readable fields a Zapier/n8n/Make
flow can branch on, with the rendered summary as one field among them. The
routing tag and any static fields come from the destination config so a
receiver can tell triggers apart when several point at one endpoint.
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class WebhookEvent(BaseModel):
    """The JSON body POSTed to a webhook destination for a fire or test event."""

    model_config = ConfigDict(frozen=True)

    event: str = "trigger.fire"
    trigger_id: str
    trigger_kind: str
    doc_path: str
    sha: str
    change_kind: str
    fired_at: str
    actor: str | None
    summary: str
    reason: str
    routing_tag: str | None = None
    fields: dict[str, str] = Field(default_factory=dict)
