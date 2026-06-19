"""HTTP request/response shapes for the Craft launch + Connect-Onyx routes."""

from __future__ import annotations

from pydantic import BaseModel, Field


class CraftConnectRequest(BaseModel):
    """Manual-PAT connect (v0): the user pastes an Onyx Personal Access Token.
    We validate it against the configured Onyx instance and store it as that
    user's credential. Capped to bound abuse; real PATs are ~250 chars."""

    pat: str = Field(..., min_length=8, max_length=1024)


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
    # The configured Onyx origin — lets the FE link the user to where they
    # mint a PAT ({base}/... Settings → Accounts & Access). Present whenever
    # Craft is available (i.e. the endpoint didn't 404).
    onyx_base_url: str | None = None
    # Dormant redirect-mint URL (future no-copy-paste connect); unused in v0.
    connect_url: str | None = None
