"""HTTP client for the Onyx build API + Connect-Onyx exchange.

Mirrors the repo's outbound-client convention (``app/slack/client.py``,
``app/web/serper.py``): synchronous ``requests`` with explicit timeouts —
callers are worker threads, not the event loop.

Security posture:
- Targets ONLY the admin-configured Onyx base URL (``validate_onyx_base_url``
  enforces https, with http allowed solely for localhost dev).
- The bearer PAT never appears in URLs or logs; error text is truncated and
  never echoes request headers.
"""

from __future__ import annotations

import logging
from typing import Any, cast
from urllib.parse import urlparse

import requests

log = logging.getLogger(__name__)

# Session create blocks on sandbox pod provisioning (verified ~10-60s,
# wake-from-sleep ~15s) — give it generous headroom.
_CREATE_TIMEOUT_S = 300
_DEFAULT_TIMEOUT_S = 60
_EXCHANGE_TIMEOUT_S = 30
_ERROR_BODY_CAP = 500

_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


class OnyxError(Exception):
    """Base for outbound Onyx failures."""


class OnyxAuthError(OnyxError):
    """401/403 — the stored PAT is invalid, expired, or revoked."""


class OnyxCapacityError(OnyxError):
    """429 — rate limited / org sandbox cap reached."""


class OnyxServerError(OnyxError):
    """5xx from Onyx."""


class OnyxUnreachableError(OnyxError):
    """Connection / timeout failure reaching Onyx."""


def validate_onyx_base_url(url: str) -> str:
    """Validate the admin-supplied Onyx origin; returns it unchanged.

    Same shape rules as PUBLIC_BASE_URL (scheme + no trailing slash), plus
    an SSRF guard: https everywhere, http only for localhost dev.
    """
    if not (url.startswith("http://") or url.startswith("https://")):
        raise ValueError(f"Onyx base URL must start with http:// or https:// (got {url!r})")
    if url.endswith("/"):
        raise ValueError(f"Onyx base URL must not have a trailing slash (got {url!r})")
    parsed = urlparse(url)
    if not parsed.hostname:
        raise ValueError(f"Onyx base URL has no host (got {url!r})")
    if parsed.scheme == "http" and parsed.hostname not in _LOCAL_HOSTS:
        raise ValueError("Onyx base URL must use https (http is allowed only for localhost dev)")
    return url


def _raise_for_status(resp: requests.Response, *, what: str) -> None:
    if resp.status_code < 400:
        return
    detail = resp.text[:_ERROR_BODY_CAP]
    if resp.status_code in (401, 403):
        raise OnyxAuthError(f"{what}: onyx returned {resp.status_code}")
    if resp.status_code == 429:
        raise OnyxCapacityError(f"{what}: onyx returned 429")
    if resp.status_code >= 500:
        raise OnyxServerError(f"{what}: onyx returned {resp.status_code}: {detail}")
    raise OnyxError(f"{what}: onyx returned {resp.status_code}: {detail}")


class OnyxClient:
    """Per-call client bound to one Onyx origin + one user's PAT."""

    def __init__(self, base_url: str, pat: str):
        self._base = validate_onyx_base_url(base_url)
        self._headers = {"Authorization": f"Bearer {pat}"}

    def _request(
        self,
        method: str,
        path: str,
        *,
        what: str,
        timeout: int = _DEFAULT_TIMEOUT_S,
        **kwargs: Any,
    ) -> requests.Response:
        url = f"{self._base}{path}"
        try:
            resp = requests.request(method, url, headers=self._headers, timeout=timeout, **kwargs)
        except (requests.ConnectionError, requests.Timeout) as e:
            raise OnyxUnreachableError(f"{what}: cannot reach onyx at {self._base}") from e
        _raise_for_status(resp, what=what)
        return resp

    # ----------------------------------------------------------------- #
    # Build API                                                          #
    # ----------------------------------------------------------------- #

    def create_build_session(self) -> str:
        """Create (or reuse) the user's empty Craft build session; blocks until
        the sandbox is up. Returns the session id. The create endpoint has no
        name field, so the session is unnamed here — call set_session_name."""
        resp = self._request(
            "POST",
            "/api/build/sessions",
            what="create build session",
            timeout=_CREATE_TIMEOUT_S,
            json={},
        )
        body: dict[str, Any] = resp.json()
        session_id = body.get("id")
        if not isinstance(session_id, str) or not session_id:
            raise OnyxError("create build session: response missing id")
        return session_id

    def set_session_name(self, session_id: str, *, name: str) -> None:
        """Set the session's display name. Without this, Onyx lists the build
        as ``Session <id>``; the create endpoint accepts no name."""
        self._request(
            "PUT",
            f"/api/build/sessions/{session_id}/name",
            what="set session name",
            json={"name": name},
        )

    def upload_attachment(self, session_id: str, *, filename: str, content: bytes) -> None:
        """Drop a file into the session sandbox's attachments/ dir."""
        self._request(
            "POST",
            f"/api/build/sessions/{session_id}/upload",
            what="upload attachment",
            files={"file": (filename, content, "text/markdown")},
        )

    def session_message_count(self, session_id: str) -> int:
        """Number of messages on the session — the seed-idempotency probe."""
        resp = self._request(
            "GET",
            f"/api/build/sessions/{session_id}/messages",
            what="list session messages",
        )
        body: dict[str, Any] = resp.json()
        messages = body.get("messages")
        if not isinstance(messages, list):
            return 0
        return len(cast("list[object]", messages))

    def send_seed_message(self, session_id: str, *, content: str) -> None:
        """Start the first agent turn. Onyx detaches the turn into its
        background runner; we only need the request to be accepted, so the
        response stream is closed immediately after the status check."""
        url = f"{self._base}/api/build/sessions/{session_id}/send-message"
        try:
            resp = requests.post(
                url,
                headers=self._headers,
                json={"content": content},
                timeout=_DEFAULT_TIMEOUT_S,
                stream=True,
            )
        except (requests.ConnectionError, requests.Timeout) as e:
            raise OnyxUnreachableError(
                f"send seed message: cannot reach onyx at {self._base}"
            ) from e
        try:
            _raise_for_status(resp, what="send seed message")
        finally:
            resp.close()

    # ----------------------------------------------------------------- #
    # Connect-Onyx account link                                          #
    # ----------------------------------------------------------------- #

    def whoami(self) -> dict[str, Any]:
        """Identify the PAT's owner — validates the token (401/403 → OnyxAuthError)
        and returns the user record (notably ``email``). Used to verify a
        pasted PAT at connect time."""
        resp = self._request("GET", "/api/me", what="whoami")
        body: dict[str, Any] = resp.json()
        return body

    def revoke_pat(self) -> None:
        """Best-effort revoke of this client's own PAT on disconnect."""
        self._request("DELETE", "/api/connect/agent-wiki", what="revoke connection")


def exchange_connect_code(base_url: str, *, code: str, code_verifier: str) -> dict[str, Any]:
    """Server-to-server: swap the one-time connect code for the raw PAT.

    Unauthenticated by design — the code itself is the single-use bearer,
    bound to the PKCE verifier. Returns ``{pat, onyx_user_email, expires_at}``.
    """
    base = validate_onyx_base_url(base_url)
    try:
        resp = requests.post(
            f"{base}/api/connect/agent-wiki/exchange",
            json={"code": code, "code_verifier": code_verifier},
            timeout=_EXCHANGE_TIMEOUT_S,
        )
    except (requests.ConnectionError, requests.Timeout) as e:
        raise OnyxUnreachableError(f"connect exchange: cannot reach onyx at {base}") from e
    _raise_for_status(resp, what="connect exchange")
    body: dict[str, Any] = resp.json()
    pat = body.get("pat")
    if not isinstance(pat, str) or not pat:
        raise OnyxError("connect exchange: response missing pat")
    return body
