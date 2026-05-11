"""Tests for the OIDC auth flow (``app/auth/oidc.py`` + ``/api/auth/oidc/*``).

Scope: the seam between authlib and our user/session layer. We stub
the authlib OIDC client at ``app.auth.oidc.client`` so the tests
don't make network calls and don't depend on a registered OAuth
client. Authlib's own state/PKCE handling is not under test here.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient
from starlette.responses import RedirectResponse

from app.auth import users as users_repo
from app.auth.oidc import upsert_oidc_user
from app.main import create_app


@pytest.fixture
def oidc_config(tmp_repo, monkeypatch):
    """Flip CONFIG into oidc mode for the duration of a test.

    ``tmp_repo`` already gives us a tmp DB + wiki repo; we just rebind
    every captured CONFIG reference to one with auth_mode="oidc".
    """
    cfg = tmp_repo.model_copy(
        update={
            "auth_mode": "oidc",
            "oidc_issuer": "https://accounts.google.com",
            "oidc_client_id": "test-client-id",
            "oidc_client_secret": "test-client-secret",
        }
    )
    monkeypatch.setattr("app.config.CONFIG", cfg)
    monkeypatch.setattr("app.api.auth.CONFIG", cfg)
    monkeypatch.setattr("app.auth.oidc.CONFIG", cfg)
    return cfg


@pytest.fixture
def client(oidc_config):
    return TestClient(create_app())


@pytest.fixture
def stub_client(monkeypatch):
    """Replace ``app.auth.oidc.client()`` with a configurable mock.

    Tests set ``stub.authorize_access_token.return_value`` to control
    the token + userinfo payload the callback handler receives. The
    Starlette authlib API is async, so the mock methods are
    ``AsyncMock`` so ``await client.authorize_access_token(...)``
    resolves to the configured value.
    """
    stub = MagicMock(name="oidc_client")
    stub.authorize_redirect = AsyncMock()
    stub.authorize_access_token = AsyncMock()
    stub.userinfo = AsyncMock()
    monkeypatch.setattr("app.api.auth.oidc_client", lambda: stub)
    return stub


# ---------------------------------------------------------------------------
# upsert_oidc_user
# ---------------------------------------------------------------------------


def test_upsert_creates_user_when_missing(tmp_repo):
    user_id = upsert_oidc_user(email="alice@example.com", name="Alice")
    row = users_repo.get_by_id(user_id)
    assert row is not None
    assert row["email"] == "alice@example.com"
    assert row["name"] == "Alice"


def test_upsert_first_user_is_admin(tmp_repo):
    user_id = upsert_oidc_user(email="first@example.com", name=None)
    row = users_repo.get_by_id(user_id)
    assert row is not None
    assert bool(row["is_admin"]) is True


def test_upsert_subsequent_users_not_admin(tmp_repo):
    upsert_oidc_user(email="first@example.com", name=None)
    second = upsert_oidc_user(email="second@example.com", name="Second")
    row = users_repo.get_by_id(second)
    assert row is not None
    assert bool(row["is_admin"]) is False


def test_upsert_existing_user_returns_same_id(tmp_repo):
    first = upsert_oidc_user(email="alice@example.com", name="Alice")
    second = upsert_oidc_user(email="alice@example.com", name="Alice (renamed)")
    assert first == second
    # Existing rows aren't overwritten — name stays as originally created.
    row = users_repo.get_by_id(first)
    assert row is not None
    assert row["name"] == "Alice"


# ---------------------------------------------------------------------------
# /api/auth/oidc/login
# ---------------------------------------------------------------------------


def test_login_disabled_when_auth_mode_basic(tmp_repo, monkeypatch):
    cfg = tmp_repo.model_copy(update={"auth_mode": "basic"})
    monkeypatch.setattr("app.api.auth.CONFIG", cfg)
    c = TestClient(create_app())
    resp = c.get("/api/auth/oidc/login")
    assert resp.status_code == 400
    assert resp.json() == {"error": "oidc disabled"}


def test_login_returns_503_when_client_not_registered(client, monkeypatch):
    monkeypatch.setattr("app.api.auth.oidc_client", lambda: None)
    resp = client.get("/api/auth/oidc/login")
    assert resp.status_code == 503


def test_login_redirects_via_authlib(client, stub_client):
    stub_client.authorize_redirect.return_value = RedirectResponse(
        url="https://idp.example/authorize?..."
    )
    resp = client.get("/api/auth/oidc/login", follow_redirects=False)
    assert resp.status_code == 307
    assert resp.headers["location"].startswith("https://idp.example/authorize")
    stub_client.authorize_redirect.assert_called_once()


# ---------------------------------------------------------------------------
# /api/auth/oidc/callback
# ---------------------------------------------------------------------------


def _userinfo(**overrides):
    base = {"email": "alice@example.com", "email_verified": True, "name": "Alice"}
    base.update(overrides)
    return base


def test_callback_creates_user_and_starts_session(client, stub_client):
    stub_client.authorize_access_token.return_value = {"userinfo": _userinfo()}
    resp = client.get("/api/auth/oidc/callback", follow_redirects=False)
    assert resp.status_code == 307
    assert resp.headers["location"] == "/"
    row = users_repo.get_by_email("alice@example.com")
    assert row is not None
    assert row["name"] == "Alice"
    # First user — should be admin per the existing convention.
    assert bool(row["is_admin"]) is True
    # And the response Set-Cookie should contain the session cookie.
    assert "session=" in resp.headers.get("set-cookie", "")


def test_callback_normalizes_email_lowercase(client, stub_client):
    stub_client.authorize_access_token.return_value = {
        "userinfo": _userinfo(email="ALICE@EXAMPLE.COM")
    }
    client.get("/api/auth/oidc/callback", follow_redirects=False)
    assert users_repo.get_by_email("alice@example.com") is not None


def test_callback_rejects_unverified_email(client, stub_client):
    stub_client.authorize_access_token.return_value = {
        "userinfo": _userinfo(email_verified=False)
    }
    resp = client.get("/api/auth/oidc/callback", follow_redirects=False)
    assert resp.status_code == 307
    assert "error=oidc_email_unverified" in resp.headers["location"]
    # No user row should have been created.
    assert users_repo.get_by_email("alice@example.com") is None


def test_callback_rejects_missing_email(client, stub_client):
    stub_client.authorize_access_token.return_value = {
        "userinfo": {"email_verified": True, "name": "Alice"}
    }
    resp = client.get("/api/auth/oidc/callback", follow_redirects=False)
    assert resp.status_code == 307
    assert "error=oidc_no_email" in resp.headers["location"]


def test_callback_rejects_email_not_in_allowlist(client, stub_client, monkeypatch):
    monkeypatch.setenv("ALLOWED_EMAILS", "*@onyx.app")
    monkeypatch.setattr("app.auth.whitelist.os.environ", {"ALLOWED_EMAILS": "*@onyx.app"})
    stub_client.authorize_access_token.return_value = {
        "userinfo": _userinfo(email="outsider@external.com")
    }
    resp = client.get("/api/auth/oidc/callback", follow_redirects=False)
    assert resp.status_code == 307
    assert "error=oidc_email_not_allowed" in resp.headers["location"]
    assert users_repo.get_by_email("outsider@external.com") is None


def test_callback_handles_exchange_failure(client, stub_client):
    stub_client.authorize_access_token.side_effect = RuntimeError("boom")
    resp = client.get("/api/auth/oidc/callback", follow_redirects=False)
    assert resp.status_code == 307
    assert "error=oidc_exchange_failed" in resp.headers["location"]


def test_callback_falls_back_to_userinfo_endpoint(client, stub_client):
    """Some IdPs put userinfo on the token; some require a separate fetch."""
    stub_client.authorize_access_token.return_value = {}  # no userinfo on token
    stub_client.userinfo.return_value = _userinfo(email="bob@example.com", name="Bob")
    resp = client.get("/api/auth/oidc/callback", follow_redirects=False)
    assert resp.status_code == 307
    assert resp.headers["location"] == "/"
    assert users_repo.get_by_email("bob@example.com") is not None
