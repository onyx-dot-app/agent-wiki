"""Admin SMTP settings: masked round-trips through /admin/email-smtp, the
service's configured gate, and the synchronous test-send endpoint with
smtplib mocked at the seam."""
from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.email import service as email_service
from app.main import create_app

from tests._auth import login_fastapi
from tests._seed import seed_user


@pytest.fixture
def admin_client(tmp_repo):
    client = TestClient(create_app())
    seed_user(uid="usr_admin", email="admin@x.com", is_admin=True)
    login_fastapi(client, "usr_admin")
    return client


def _put(client, **body: Any):
    r = client.put("/api/admin/email-smtp", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def test_settings_round_trip_masks_password(admin_client):
    view = _put(
        admin_client,
        host="smtp.gmail.com", port=587, username="wiki@x.com",
        password="app-password-123", from_address="wiki@x.com",
    )
    assert view["host"] == "smtp.gmail.com"
    assert view["password_set"] is True
    assert "app-password-123" not in str(view)

    # Omitted/empty password keeps the stored one; null clears it.
    view = _put(admin_client, host="smtp.other.com")
    assert view["host"] == "smtp.other.com"
    assert view["password_set"] is True
    view = _put(admin_client, password=None)
    assert view["password_set"] is False


def test_send_requires_configuration(tmp_repo):
    with pytest.raises(email_service.EmailNotConfiguredError):
        email_service.send(to="a@b.com", subject="s", text="t")


class _FakeSMTP:
    """Captures the login and message instead of talking to a server."""

    sent: list[dict[str, Any]] = []

    def __init__(self, host, port, timeout=None, context=None):
        self.host, self.port = host, port

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def starttls(self, context=None):
        self.tls = True

    def login(self, username, password):
        self.creds = (username, password)

    def send_message(self, msg):
        _FakeSMTP.sent.append(
            {"host": self.host, "creds": getattr(self, "creds", None), "msg": msg}
        )


def test_test_endpoint_sends_via_smtp_seam(admin_client, monkeypatch):
    _put(
        admin_client,
        host="smtp.gmail.com", port=587, username="wiki@x.com",
        password="pw", from_address="wiki@x.com",
    )
    _FakeSMTP.sent = []
    monkeypatch.setattr(email_service.smtplib, "SMTP", _FakeSMTP)

    r = admin_client.post("/api/admin/email-smtp/test", json={"to": ""})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True, body
    assert "admin@x.com" in body["detail"]  # blank recipient falls back to the actor

    [record] = _FakeSMTP.sent
    assert record["creds"] == ("wiki@x.com", "pw")
    msg = record["msg"]
    assert msg["To"] == "admin@x.com"
    assert msg["From"] == "wiki@x.com"
    assert "test" in msg["Subject"].lower()


def test_test_endpoint_reports_unconfigured(admin_client):
    r = admin_client.post("/api/admin/email-smtp/test", json={})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert "not configured" in body["detail"]


def test_endpoints_require_admin(tmp_repo):
    client = TestClient(create_app())
    seed_user(uid="usr_plain", email="p@x.com")
    login_fastapi(client, "usr_plain")
    assert client.get("/api/admin/email-smtp").status_code == 403
    assert client.post("/api/admin/email-smtp/test", json={}).status_code == 403
