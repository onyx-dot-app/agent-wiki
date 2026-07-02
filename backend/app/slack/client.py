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
