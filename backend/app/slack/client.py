"""Slack incoming-webhook client.

A thin wrapper over a single ``POST`` to a Slack incoming webhook URL,
following the outbound-HTTP convention in ``app/web/serper.py`` (sync
``requests``, explicit timeout, error → typed exception). The caller (the
trigger dispatcher) decides whether to swallow failures so a fire is never
lost just because Slack is unreachable.
"""
from __future__ import annotations

import logging
from typing import Any, cast

import requests

log = logging.getLogger(__name__)

_REQUEST_TIMEOUT_SECONDS = 15


class SlackApiError(RuntimeError):
    pass


def _call_api(bot_token: str, method: str, payload: dict[str, Any]) -> dict[str, Any]:
    """POST a Slack Web API method with the bot token. Slack signals failure
    with HTTP 200 and ``{"ok": false, "error": ...}``, so both transport
    errors and ok=false raise :class:`SlackApiError`."""
    try:
        response = requests.post(
            f"https://slack.com/api/{method}",
            headers={"Authorization": f"Bearer {bot_token}"},
            json=payload,
            timeout=_REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        body = cast(dict[str, Any], response.json())
    except requests.RequestException as exc:
        raise SlackApiError(f"{method} failed: {exc}") from exc
    if not body.get("ok"):
        raise SlackApiError(f"{method} rejected: {body.get('error', 'unknown')}")
    return body


def post_chat_message(*, bot_token: str, channel: str, text: str) -> None:
    """Post ``text`` to a channel (or DM channel) as the bot."""
    _call_api(bot_token, "chat.postMessage", {"channel": channel, "text": text})


def open_dm(*, bot_token: str, slack_user_id: str) -> str:
    """Open (or fetch) the bot's DM channel with a user; returns its id."""
    body = _call_api(bot_token, "conversations.open", {"users": slack_user_id})
    channel = cast(dict[str, Any], body.get("channel") or {})
    channel_id = channel.get("id")
    if not isinstance(channel_id, str) or not channel_id:
        raise SlackApiError("conversations.open returned no channel id")
    return channel_id


def list_channels(*, bot_token: str) -> list[dict[str, Any]]:
    """Channels the token can see, for the trigger channel picker. One page of
    200 covers the picker use case; private channels appear only where the bot
    is a member."""
    body = _call_api(
        bot_token,
        "conversations.list",
        {
            "types": "public_channel,private_channel",
            "exclude_archived": True,
            "limit": 200,
        },
    )
    out: list[dict[str, Any]] = []
    for ch in cast(list[dict[str, Any]], body.get("channels") or []):
        out.append(
            {
                "id": ch.get("id"),
                "name": ch.get("name"),
                "is_private": bool(ch.get("is_private")),
            }
        )
    return out


def exchange_oauth_code(
    *, client_id: str, client_secret: str, code: str, redirect_uri: str
) -> dict[str, Any]:
    """Exchange an OAuth authorization code at ``oauth.v2.access``.

    Returns Slack's parsed response body (bot ``access_token``, ``scope``,
    ``team``, ``authed_user``). Slack signals failure with HTTP 200 and
    ``{"ok": false, "error": ...}``, so both transport errors and ok=false
    raise :class:`SlackApiError`.
    """
    try:
        response = requests.post(
            "https://slack.com/api/oauth.v2.access",
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "code": code,
                "redirect_uri": redirect_uri,
            },
            timeout=_REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        body = cast(dict[str, Any], response.json())
    except requests.RequestException as exc:
        raise SlackApiError(f"oauth exchange failed: {exc}") from exc
    if not body.get("ok"):
        raise SlackApiError(f"oauth exchange rejected: {body.get('error', 'unknown')}")
    return body


def post_message(*, webhook_url: str, text: str) -> None:
    """POST ``text`` to a Slack incoming webhook.

    Raises :class:`SlackApiError` on a network failure or a non-2xx
    response. Slack incoming webhooks return ``200 ok`` on success and a
    plain-text error body (e.g. ``invalid_payload``, ``no_service``) with a
    4xx on failure.
    """
    if not webhook_url:
        raise ValueError("Slack webhook_url is required")

    try:
        response = requests.post(
            webhook_url,
            headers={"Content-Type": "application/json"},
            json={"text": text},
            timeout=_REQUEST_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        raise SlackApiError(f"slack webhook request failed: {exc}") from exc

    if response.status_code == 401 or response.status_code == 403:
        raise SlackApiError("slack rejected the webhook URL")
    if response.status_code >= 400:
        raise SlackApiError(
            f"slack returned status {response.status_code}: {response.text[:200]}"
        )
