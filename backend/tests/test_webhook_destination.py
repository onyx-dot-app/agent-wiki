"""Webhook destination: the SSRF guard, the HMAC signer, and delivery.

Guards the security-critical seam of PR 9: user-supplied webhook URLs must
never reach private/loopback/metadata hosts, and every body must carry an
HMAC signature a receiver can verify.
"""
from __future__ import annotations

import hashlib
import hmac

import pytest

from app.net.ssrf import UnsafeUrlError, assert_public_url
from app.webhooks import client as webhook_client


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/hook",
        "http://localhost/hook",
        "http://10.1.2.3/hook",
        "http://192.168.0.5/hook",
        "http://169.254.169.254/latest/meta-data",  # cloud metadata
        "ftp://example.com/hook",  # non-http scheme
        "http:///nohost",
    ],
)
def test_assert_public_url_rejects_unsafe(url: str) -> None:
    with pytest.raises(UnsafeUrlError):
        assert_public_url(url)


def test_assert_public_url_accepts_public(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.net.ssrf.socket.getaddrinfo",
        lambda *a, **k: [(2, 1, 6, "", ("93.184.216.34", 443))],
    )
    assert_public_url("https://example.com/hook")  # does not raise


def test_sign_is_hmac_sha256_with_scheme_prefix() -> None:
    body = b'{"event":"trigger.fire"}'
    sig = webhook_client.sign("secret", body)
    expected = hmac.new(b"secret", body, hashlib.sha256).hexdigest()
    assert sig == f"sha256={expected}"


def test_deliver_blocks_unsafe_url() -> None:
    with pytest.raises(UnsafeUrlError):
        webhook_client.deliver(url="http://127.0.0.1/hook", body=b"{}")


def test_deliver_signs_and_posts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.net.ssrf.socket.getaddrinfo",
        lambda *a, **k: [(2, 1, 6, "", ("93.184.216.34", 443))],
    )
    sent: dict[str, object] = {}

    class _Resp:
        status_code = 200

    def fake_post(url: str, **kw: object) -> _Resp:
        sent["url"] = url
        sent["headers"] = kw.get("headers")
        sent["data"] = kw.get("data")
        return _Resp()

    monkeypatch.setattr("app.webhooks.client.requests.post", fake_post)
    body = b'{"event":"trigger.fire"}'
    webhook_client.deliver(
        url="https://example.com/hook",
        body=body,
        headers={"X-Custom": "1"},
        secret="shh",
    )
    headers = sent["headers"]
    assert isinstance(headers, dict)
    assert headers["X-Custom"] == "1"
    assert headers["Content-Type"] == "application/json"
    assert headers[webhook_client.SIGNATURE_HEADER] == webhook_client.sign("shh", body)
    assert sent["data"] == body


def test_deliver_raises_on_non_2xx(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.net.ssrf.socket.getaddrinfo",
        lambda *a, **k: [(2, 1, 6, "", ("93.184.216.34", 443))],
    )

    class _Resp:
        status_code = 500

    monkeypatch.setattr(
        "app.webhooks.client.requests.post", lambda *a, **k: _Resp()
    )
    with pytest.raises(webhook_client.WebhookError):
        webhook_client.deliver(url="https://example.com/hook", body=b"{}")
