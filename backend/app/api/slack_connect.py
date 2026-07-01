"""Connect-Slack OAuth flow, mirroring the Craft connect shape.

Paths match the redirect URL registered on the Slack app
(``/api/connectors/slack/callback``), so the app config never needs editing.
The flow is dark (404) until an admin sets the app credentials at /admin/slack.
"""
from __future__ import annotations

import logging
from typing import Any, cast
from urllib.parse import quote, urlencode

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from app.auth import User
from app.auth.deps import require_user
from app.config import CONFIG
from app.slack import app_settings, client as slack_client, connect_state, connections

router = APIRouter()
log = logging.getLogger(__name__)

# Must stay a subset of the scopes on the registered app manifest.
_BOT_SCOPES = "chat:write,chat:write.public,channels:read,groups:read,im:write,users:read,channels:join"


class SlackConnectStatus(BaseModel):
    configured: bool
    connected: bool
    team_name: str | None = None
    token_display: str | None = None
    connect_url: str | None = None


def _require_configured() -> app_settings.SlackAppSettings:
    """404 while the feature is dark; otherwise the app credentials."""
    settings = app_settings.get()
    if not settings.configured:
        raise HTTPException(status_code=404, detail="slack app not configured")
    return settings


def _callback_url() -> str:
    return f"{CONFIG.public_base_url}/api/connectors/slack/callback"


def _bounce(return_to: str | None, *, outcome: str) -> RedirectResponse:
    """Send the browser back into the app after the connect flow."""
    path = connect_state.normalize_return_to(return_to) or "/"
    sep = "&" if "?" in path else "?"
    return RedirectResponse(
        f"{CONFIG.public_base_url}{path}{sep}slack_connect={outcome}", status_code=302
    )


@router.get("", response_model=SlackConnectStatus)
def get_status(user: User = Depends(require_user)) -> SlackConnectStatus:
    settings = app_settings.get()
    rows = connections.list_for_user(user.id)
    first = rows[0] if rows else None
    connect_url = None
    if settings.configured:
        connect_url = f"{CONFIG.public_base_url}/api/connectors/slack/start"
    return SlackConnectStatus(
        configured=settings.configured,
        connected=first is not None,
        team_name=first["team_name"] if first else None,
        token_display=first["token_display"] if first else None,
        connect_url=connect_url,
    )


@router.get("/start")
def connect_start(
    return_to: str | None = None,
    user: User = Depends(require_user),
) -> RedirectResponse:
    settings = _require_configured()
    state = connect_state.mint_state(user_id=user.id, return_to=return_to)
    query = urlencode(
        {
            "client_id": settings.client_id,
            "scope": _BOT_SCOPES,
            "state": state,
            "redirect_uri": _callback_url(),
        },
        quote_via=quote,
    )
    return RedirectResponse(
        f"https://slack.com/oauth/v2/authorize?{query}", status_code=302
    )


@router.get("/callback")
def connect_callback(
    state: str,
    code: str | None = None,
    error: str | None = None,
    user: User = Depends(require_user),
) -> RedirectResponse:
    settings = _require_configured()
    claimed = connect_state.consume_state(state, user_id=user.id)
    if claimed is None:
        log.warning("slack connect callback rejected (bad state) user=%s", user.id)
        return _bounce(None, outcome="error")
    return_to = claimed["return_to"]
    if error or not code:
        # Slack sends error=access_denied when the user cancels the consent.
        log.info("slack connect declined user=%s error=%s", user.id, error)
        return _bounce(return_to, outcome="declined")
    try:
        body = slack_client.exchange_oauth_code(
            client_id=settings.client_id,
            client_secret=settings.client_secret,
            code=code,
            redirect_uri=_callback_url(),
        )
    except slack_client.SlackApiError:
        log.exception("slack connect exchange failed user=%s", user.id)
        return _bounce(return_to, outcome="error")
    team = cast(dict[str, Any], body.get("team") or {})
    authed_user = cast(dict[str, Any], body.get("authed_user") or {})
    connections.upsert(
        user_id=user.id,
        team_id=str(team.get("id") or ""),
        team_name=cast(str | None, team.get("name")),
        slack_user_id=str(authed_user.get("id") or ""),
        bot_token=str(body["access_token"]),
        scope=cast(str | None, body.get("scope")),
    )
    return _bounce(return_to, outcome="ok")


@router.delete("")
def disconnect(user: User = Depends(require_user)) -> dict[str, bool]:
    """Drop the caller's connection rows.

    Deliberately no ``auth.revoke``: the bot token is workspace-shared, so
    revoking it would sever every other connected user in the workspace.
    """
    removed = False
    for row in connections.list_for_user(user.id):
        removed = connections.delete_connection(user.id, row["team_id"]) or removed
    return {"disconnected": removed}
