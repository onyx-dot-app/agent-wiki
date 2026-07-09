"""Outbound sender for webhook destinations.

One place that signs and POSTs a structured event to a user-supplied URL:
the trigger dispatcher and the "send test event" endpoint both call
:func:`deliver`. Every send runs through the SSRF guard first and carries an
HMAC-SHA256 signature over the exact body bytes so a receiver can confirm the
call came from us.
"""
from __future__ import annotations

import hashlib
import hmac

import requests

from app.net.ssrf import assert_public_url

_TIMEOUT_SECONDS = 10
SIGNATURE_HEADER = "X-AgentWiki-Signature"


class WebhookError(RuntimeError):
    """A webhook POST failed (network error or non-2xx response)."""


def sign(secret: str, body: bytes) -> str:
    """HMAC-SHA256 of the body under the config's signing secret, hex-encoded
    and prefixed so the scheme is explicit to receivers."""
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def deliver(
    *,
    url: str,
    body: bytes,
    headers: dict[str, str] | None = None,
    secret: str | None = None,
) -> None:
    """POST ``body`` to ``url`` with a JSON content type, the caller's custom
    headers, and an HMAC signature header when a secret is set. Raises
    :class:`~app.net.ssrf.UnsafeUrlError` on an unsafe URL, or
    :class:`WebhookError` on a network failure or non-2xx response."""
    assert_public_url(url)
    request_headers = {"Content-Type": "application/json", **(headers or {})}
    if secret:
        request_headers[SIGNATURE_HEADER] = sign(secret, body)
    try:
        response = requests.post(
            url, data=body, headers=request_headers, timeout=_TIMEOUT_SECONDS
        )
    except requests.RequestException as e:
        raise WebhookError(f"webhook POST failed: {e}") from e
    if response.status_code >= 400:
        raise WebhookError(f"webhook returned {response.status_code}")
