"""SSRF guard for outbound requests to user-supplied URLs.

Webhook destinations POST to a URL the user typed, so the URL must be
proven to point at a public host before we send. ``assert_public_url``
parses the URL, requires http/https, resolves the host, and rejects any
resolved address that is private, loopback, link-local, multicast,
reserved, or the cloud metadata endpoint (169.254.169.254).

Limitation: the resolve-then-send window leaves a DNS-rebinding gap (the
name could resolve to a public IP here and a private one at request
time). Pinning the checked IP into the transport closes it and is the
deferred hardening; the resolve check blocks the ordinary SSRF attempts.
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlparse


class UnsafeUrlError(ValueError):
    """The URL is malformed or resolves to a non-public address."""


def _ip_is_public(ip: str) -> bool:
    addr = ipaddress.ip_address(ip)
    # is_global is False for private, loopback, link-local (incl. the
    # 169.254.169.254 metadata endpoint), multicast, reserved, and
    # unspecified ranges — exactly the set we refuse.
    return addr.is_global


def assert_public_url(url: str) -> None:
    """Raise ``UnsafeUrlError`` unless ``url`` is an http(s) URL whose host
    resolves solely to public addresses."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise UnsafeUrlError("webhook URL must be http or https")
    host = parsed.hostname
    if not host:
        raise UnsafeUrlError("webhook URL has no host")

    try:
        infos = socket.getaddrinfo(host, parsed.port or None, proto=socket.IPPROTO_TCP)
    except socket.gaierror as e:
        raise UnsafeUrlError(f"webhook host did not resolve: {host}") from e

    addresses = {str(info[4][0]) for info in infos}
    if not addresses:
        raise UnsafeUrlError(f"webhook host did not resolve: {host}")
    for ip in addresses:
        if not _ip_is_public(ip):
            raise UnsafeUrlError(f"webhook host resolves to a non-public address: {host}")
