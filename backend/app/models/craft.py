"""HTTP request/response shapes for the Craft launch + Connect-Onyx routes."""

from __future__ import annotations

from pydantic import BaseModel, Field


class CraftLaunchRequest(BaseModel):
    wiki_path: str | None = None
    # Same cap as the CLI launch path — bounds first_turn_prompt padding.
    message: str = Field(..., max_length=16_384)


class CraftLaunchResponse(BaseModel):
    agent_session_id: str
    status: str


class CraftConnectStatus(BaseModel):
    """GET /api/craft/connect — is this user linked to Onyx, and as whom."""

    connected: bool
    onyx_user_email: str | None = None
    token_display: str | None = None
    expires_at: str | None = None
    # Where the frontend sends the browser to (re)connect. Present whenever
    # Craft is available, connected or not.
    connect_url: str | None = None
