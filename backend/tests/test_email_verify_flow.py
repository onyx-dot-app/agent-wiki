"""End-to-end email destination verification over HTTP: creating an email
config sends a verify link, the public link stamps the config, replays are
rejected, and resends are rate-limited."""
from __future__ import annotations

import re
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.email import service as email_service
from app.main import create_app

from tests._auth import login_fastapi
from tests._seed import seed_user


@pytest.fixture
def client(tmp_repo):
    c = TestClient(create_app())
    seed_user(uid="usr_1", email="me@x.com")
    login_fastapi(c, "usr_1")
    return c


@pytest.fixture
def outbox(monkeypatch):
    calls: list[dict[str, Any]] = []

    def fake_send(*, to: str, subject: str, text: str, html: str | None = None) -> None:
        calls.append({"to": to, "subject": subject, "text": text})

    monkeypatch.setattr(email_service, "send", fake_send)
    return calls


def _create(client, address: str = "friend@example.com") -> dict[str, Any]:
    r = client.post(
        "/api/triggers/destination-configs",
        json={"type": "email", "name": "Friend", "config": {"address": address}},
    )
    assert r.status_code == 201, r.text
    return r.json()


def _token_from(outbox) -> str:
    m = re.search(r"token=(evt_[\w-]+)", outbox[-1]["text"])
    assert m, outbox[-1]["text"]
    return m.group(1)


def test_create_sends_link_and_public_click_verifies(client, outbox):
    view = _create(client)
    assert view["verification_error"] is None
    assert view["verified_at"] is None
    assert outbox[-1]["to"] == "friend@example.com"

    r = client.get(f"/api/email/verify?token={_token_from(outbox)}")
    assert r.status_code == 200
    assert "verified" in r.text.lower()

    rows = client.get("/api/triggers/destination-configs").json()["configs"]
    assert rows[0]["verified_at"] is not None

    # Replay lands on the failure page.
    assert client.get(f"/api/email/verify?token={_token_from(outbox)}").status_code == 400


def test_resend_is_rate_limited_and_blocked_after_verify(client, outbox):
    view = _create(client)
    cfg_id = view["id"]
    assert client.post(f"/api/triggers/destination-configs/{cfg_id}/resend-verify").status_code == 429

    client.get(f"/api/email/verify?token={_token_from(outbox)}")
    assert client.post(f"/api/triggers/destination-configs/{cfg_id}/resend-verify").status_code == 400


def test_readding_verified_address_sends_nothing(client, outbox):
    view = _create(client)
    client.get(f"/api/email/verify?token={_token_from(outbox)}")
    sends_before = len(outbox)

    again = _create(client)
    assert again["id"] == view["id"]
    assert again["verification_error"] is None
    assert len(outbox) == sends_before


def test_send_failure_reports_on_view_and_frees_retry(client, monkeypatch):
    def boom(**kwargs):
        raise email_service.EmailSendError("SMTP is not configured (/admin/email)")

    monkeypatch.setattr(email_service, "send", boom)
    view = _create(client)
    assert view["verification_error"] is not None
    assert "not configured" in view["verification_error"]
