"""Slack incoming-webhook client.

A thin wrapper over a single ``POST`` to a Slack incoming webhook URL,
following the outbound-HTTP convention in ``app/web/serper.py`` (sync
``requests``, explicit timeout, error → typed exception). The caller (the
trigger dispatcher) decides whether to swallow failures so a fire is never
lost just because Slack is unreachable.
"""
from __future__ import annotations

import logging
import re
import time
from typing import Any, cast

import requests

log = logging.getLogger(__name__)

# Standard markdown -> Slack mrkdwn, the high-value cases only: Slack renders
# **bold**, [text](url), and `- item` list markers literally (mrkdwn has no
# list syntax — real bullet characters are the convention). Heading markers
# are stripped to plain text (notifications carry no headings), before bold
# so `## **x**` can't nest emphasis.
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")
_HEADING_RE = re.compile(r"^#{1,6}\s+(.+)$", re.MULTILINE)
# [ \t]+ not \s+: a bare marker line must not consume its own newline.
_BULLET_RE = re.compile(r"^([ \t]*)[-*+][ \t]+", re.MULTILINE)


def to_mrkdwn(text: str) -> str:
    """Convert common markdown constructs to Slack's mrkdwn dialect."""
    text = _HEADING_RE.sub(r"\1", text)
    text = _BULLET_RE.sub(r"\1• ", text)
    text = _BOLD_RE.sub(r"*\1*", text)
    return _LINK_RE.sub(r"<\2|\1>", text)

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
    """Post ``text`` to a channel (or DM channel) as the bot. Link unfurls
    are suppressed — notification posts should not grow preview cards."""
    _call_api(
        bot_token,
        "chat.postMessage",
        {
            "channel": channel,
            "text": text,
            "unfurl_links": False,
            "unfurl_media": False,
        },
    )


def open_dm(*, bot_token: str, slack_user_id: str) -> str:
    """Open (or fetch) the bot's DM channel with a user; returns its id."""
    body = _call_api(bot_token, "conversations.open", {"users": slack_user_id})
    channel = cast(dict[str, Any], body.get("channel") or {})
    channel_id = channel.get("id")
    if not isinstance(channel_id, str) or not channel_id:
        raise SlackApiError("conversations.open returned no channel id")
    return channel_id


_GET_ATTEMPTS = 3
_GET_TIMEOUT_SECONDS = 30  # bulk reads (conversations.list) run slow on big workspaces
_MAX_RETRY_AFTER_SECONDS = 30


def _call_api_get(bot_token: str, method: str, params: dict[str, str]) -> dict[str, Any]:
    """GET a Slack Web API read method with query params. Read methods take
    form/query encoding, not JSON bodies (which they silently ignore).
    Retries timeouts/connection errors with backoff and honors Retry-After
    on 429s."""
    last_exc: Exception | None = None
    for attempt in range(_GET_ATTEMPTS):
        try:
            response = requests.get(
                f"https://slack.com/api/{method}",
                headers={"Authorization": f"Bearer {bot_token}"},
                params=params,
                timeout=_GET_TIMEOUT_SECONDS,
            )
            if response.status_code == 429:
                retry_after = min(
                    int(response.headers.get("Retry-After", "1") or 1),
                    _MAX_RETRY_AFTER_SECONDS,
                )
                log.info("%s rate limited; retrying in %ds", method, retry_after)
                time.sleep(retry_after)
                continue
            response.raise_for_status()
            body = cast(dict[str, Any], response.json())
        except (requests.Timeout, requests.ConnectionError) as exc:
            last_exc = exc
            log.warning("%s attempt %d/%d failed: %s", method, attempt + 1, _GET_ATTEMPTS, exc)
            time.sleep(2**attempt)
            continue
        except requests.RequestException as exc:
            raise SlackApiError(f"{method} failed: {exc}") from exc
        if not body.get("ok"):
            raise SlackApiError(f"{method} rejected: {body.get('error', 'unknown')}")
        return body
    raise SlackApiError(f"{method} failed after {_GET_ATTEMPTS} attempts: {last_exc}")


# Hard ceiling on picker pagination: 10 pages of 1000 = 10k channels.
_CHANNEL_PAGE_LIMIT = 10


def list_channels(*, bot_token: str) -> list[dict[str, Any]]:
    """Every channel the token can deliver to, for the picker: all non-archived
    public channels plus private ones the bot is a member of. Paginates the
    full cursor chain and returns the list sorted by name."""
    out: list[dict[str, Any]] = []
    cursor = ""
    for _ in range(_CHANNEL_PAGE_LIMIT):
        params = {
            "types": "public_channel,private_channel",
            "exclude_archived": "true",
            "limit": "1000",
        }
        if cursor:
            params["cursor"] = cursor
        body = _call_api_get(bot_token, "conversations.list", params)
        for ch in cast(list[dict[str, Any]], body.get("channels") or []):
            ch_id = ch.get("id")
            ch_name = ch.get("name")
            if not isinstance(ch_id, str) or not isinstance(ch_name, str):
                continue  # unusable in the picker without both
            out.append(
                {"id": ch_id, "name": ch_name, "is_private": bool(ch.get("is_private"))}
            )
        meta = cast(dict[str, Any], body.get("response_metadata") or {})
        cursor = str(meta.get("next_cursor") or "")
        if not cursor:
            break
    else:
        log.warning("conversations.list pagination hit the %d-page cap", _CHANNEL_PAGE_LIMIT)
    return sorted(out, key=lambda c: str(c["name"]))


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
