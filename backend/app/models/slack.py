"""HTTP shapes for the per-user Slack webhook registry.

Backs the ``/triggers/slack-webhooks`` CRUD surface. The ``webhook_url`` is a
secret: it's accepted on create but only ever returned as a masked hint.
"""
from __future__ import annotations

from pydantic import BaseModel, Field


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
