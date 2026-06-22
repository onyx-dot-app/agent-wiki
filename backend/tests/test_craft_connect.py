"""Connect-Onyx handshake + availability gate + admin Onyx Connection config."""

from __future__ import annotations

from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

from app.db.session import init_db
from app.ingest import settings as ingest_settings
from app.main import create_app
from app.onyx import connections
from app.onyx.client import OnyxAuthError, OnyxError, validate_onyx_base_url

from tests._auth import login_fastapi
from tests._seed import seed_user

ONYX = "https://onyx.example.com"


@pytest.fixture
def client(tmp_config):
    init_db()
    return TestClient(create_app())


def _configure_onyx(base: str | None = ONYX) -> None:
    ingest_settings.upsert(max_doc_chars=100_000, onyx_base_url=base)


# --------------------------------------------------------------------------- #
# Availability gate                                                           #
# --------------------------------------------------------------------------- #


def test_craft_routes_dark_without_base_url(client):
    uid = seed_user()
    login_fastapi(client, uid)
    res = client.get("/api/craft/connect")
    assert res.status_code == 404


# --------------------------------------------------------------------------- #
# Manual-PAT connect (v0)                                                     #
# --------------------------------------------------------------------------- #


def _patch_onyx_client(monkeypatch, *, email: str | None = None, error: Exception | None = None):
    """Patch app.api.craft.OnyxClient so whoami() returns/raises as configured."""

    class Fake:
        def __init__(self, base_url: str, pat: str):
            self.pat = pat

        def whoami(self) -> dict:
            if error is not None:
                raise error
            return {"email": email}

    monkeypatch.setattr("app.api.craft.OnyxClient", Fake)


def test_connect_with_valid_pat_stores_connection(client, monkeypatch):
    _configure_onyx()
    uid = seed_user()
    login_fastapi(client, uid)
    _patch_onyx_client(monkeypatch, email="nik@onyx.app")

    res = client.post("/api/craft/connect", json={"pat": "onyx_pat_" + "z" * 40})
    assert res.status_code == 200
    body = res.json()
    assert body["connected"] is True
    assert body["onyx_user_email"] == "nik@onyx.app"
    assert "z" * 40 not in (body["token_hint"] or "")

    stored = connections.status(uid)
    assert stored is not None and stored["onyx_user_email"] == "nik@onyx.app"
    # Stored PAT must round-trip (decrypts) and be usable by the launcher.
    full = connections.get_with_pat(uid, onyx_base_url=ONYX)
    assert full is not None and full["onyx_pat"] == "onyx_pat_" + "z" * 40


def test_connect_with_invalid_pat_rejected(client, monkeypatch):
    _configure_onyx()
    uid = seed_user()
    login_fastapi(client, uid)
    _patch_onyx_client(monkeypatch, error=OnyxAuthError("401"))

    res = client.post("/api/craft/connect", json={"pat": "onyx_pat_bad"})
    assert res.status_code == 401
    assert res.json()["error"] == "invalid_onyx_pat"
    assert connections.status(uid) is None


def test_connect_requires_available(client, tmp_config, monkeypatch):
    # No onyx_base_url configured → 404 even with a would-be-valid PAT.
    uid = seed_user()
    login_fastapi(client, uid)
    _patch_onyx_client(monkeypatch, email="nik@onyx.app")
    assert client.post("/api/craft/connect", json={"pat": "onyx_pat_x"}).status_code == 404


def test_connect_status_drops_expired_connection(client):
    _configure_onyx()
    uid = seed_user()
    login_fastapi(client, uid)
    # Seed an expired connection (expires_at strictly in the past).
    connections.upsert(
        user_id=uid,
        onyx_pat="onyx_pat_" + "d" * 40,
        onyx_user_email="nik@onyx.app",
        expires_at="2000-01-01 00:00:00",
        onyx_base_url=ONYX,
    )

    res = client.get("/api/craft/connect")
    assert res.status_code == 200
    body = res.json()
    assert body["connected"] is False
    # The expired row is removed so the user is prompted to reconnect.
    assert connections.status(uid) is None


# --------------------------------------------------------------------------- #
# Connect start → Onyx authorize redirect (dormant redirect-mint flow)        #
# --------------------------------------------------------------------------- #


def test_connect_start_redirects_with_state_and_pkce(client):
    _configure_onyx()
    uid = seed_user()
    login_fastapi(client, uid)
    res = client.get(
        "/api/craft/connect/start",
        params={"return_to": "/app/wiki/Some%20Page.md"},
        follow_redirects=False,
    )
    assert res.status_code == 302
    loc = urlparse(res.headers["location"])
    assert f"{loc.scheme}://{loc.netloc}" == ONYX
    assert loc.path == "/connect/agent-wiki"
    q = parse_qs(loc.query)
    assert q["redirect_uri"] == ["http://testserver/api/craft/connect/callback"]
    assert q["code_challenge_method"] == ["S256"]
    assert q["state"][0].startswith("cs_")
    assert len(q["code_challenge"][0]) == 43  # b64url(sha256) without padding


# --------------------------------------------------------------------------- #
# Callback                                                                    #
# --------------------------------------------------------------------------- #


def _start_and_get_state(client) -> str:
    res = client.get("/api/craft/connect/start", follow_redirects=False)
    q = parse_qs(urlparse(res.headers["location"]).query)
    return q["state"][0]


def test_connect_callback_stores_connection(client, monkeypatch):
    _configure_onyx()
    uid = seed_user()
    login_fastapi(client, uid)
    state = _start_and_get_state(client)

    seen: dict[str, Any] = {}

    def fake_exchange(base: str, *, code: str, code_verifier: str) -> dict[str, Any]:
        seen.update(base=base, code=code, code_verifier=code_verifier)
        return {"pat": "onyx_pat_" + "a" * 40, "onyx_user_email": "nik@onyx.app"}

    monkeypatch.setattr("app.api.craft.exchange_connect_code", fake_exchange)
    res = client.get(
        "/api/craft/connect/callback",
        params={"code": "c_1", "state": state},
        follow_redirects=False,
    )
    assert res.status_code == 302
    assert "onyx_connect=ok" in res.headers["location"]
    assert seen["base"] == ONYX
    assert seen["code"] == "c_1"
    assert len(seen["code_verifier"]) >= 43

    status = connections.status(uid)
    assert status is not None
    assert status["onyx_user_email"] == "nik@onyx.app"
    assert "…" in status["token_display"]
    assert "a" * 40 not in status["token_display"]

    via_api = client.get("/api/craft/connect").json()
    assert via_api["connected"] is True
    assert via_api["onyx_user_email"] == "nik@onyx.app"


def test_connect_callback_rejects_unknown_state(client, monkeypatch):
    _configure_onyx()
    uid = seed_user()
    login_fastapi(client, uid)
    monkeypatch.setattr(
        "app.api.craft.exchange_connect_code",
        lambda *a, **k: pytest.fail("exchange must not be called on bad state"),
    )
    res = client.get(
        "/api/craft/connect/callback",
        params={"code": "c", "state": "cs_bogus"},
        follow_redirects=False,
    )
    assert res.status_code == 302
    assert "onyx_connect=error" in res.headers["location"]
    assert connections.status(uid) is None


def test_connect_state_is_single_use(client, monkeypatch):
    _configure_onyx()
    uid = seed_user()
    login_fastapi(client, uid)
    state = _start_and_get_state(client)
    monkeypatch.setattr(
        "app.api.craft.exchange_connect_code",
        lambda *a, **k: {"pat": "onyx_pat_" + "b" * 40},
    )
    first = client.get(
        "/api/craft/connect/callback",
        params={"code": "c", "state": state},
        follow_redirects=False,
    )
    assert "onyx_connect=ok" in first.headers["location"]
    replay = client.get(
        "/api/craft/connect/callback",
        params={"code": "c", "state": state},
        follow_redirects=False,
    )
    assert "onyx_connect=error" in replay.headers["location"]


def test_connect_state_bound_to_user(client, monkeypatch):
    _configure_onyx()
    alice = seed_user(uid="alice", email="a@x.com")
    bob = seed_user(uid="bob", email="b@x.com")
    login_fastapi(client, alice)
    state = _start_and_get_state(client)

    login_fastapi(client, bob)
    monkeypatch.setattr(
        "app.api.craft.exchange_connect_code",
        lambda *a, **k: pytest.fail("exchange must not run for a foreign state"),
    )
    res = client.get(
        "/api/craft/connect/callback",
        params={"code": "c", "state": state},
        follow_redirects=False,
    )
    assert "onyx_connect=error" in res.headers["location"]
    assert connections.status(bob) is None


# --------------------------------------------------------------------------- #
# Disconnect                                                                  #
# --------------------------------------------------------------------------- #


def _connect(uid: str) -> None:
    connections.upsert(
        user_id=uid,
        onyx_pat="onyx_pat_" + "c" * 40,
        onyx_user_email="nik@onyx.app",
        expires_at=None,
        onyx_base_url=ONYX,
    )


def test_disconnect_removes_row_even_when_revoke_fails(client, monkeypatch):
    _configure_onyx()
    uid = seed_user()
    login_fastapi(client, uid)
    _connect(uid)

    class ExplodingClient:
        def __init__(self, base_url: str, pat: str):
            pass

        def revoke_pat(self) -> None:
            raise OnyxError("boom")

    monkeypatch.setattr("app.api.craft.OnyxClient", ExplodingClient)
    res = client.delete("/api/craft/connect")
    assert res.status_code == 200
    assert res.json()["disconnected"] is True
    assert connections.status(uid) is None


# --------------------------------------------------------------------------- #
# Admin "Onyx Connection" config + URL validation                             #
# --------------------------------------------------------------------------- #


def test_admin_put_validates_onyx_base_url(client):
    admin = seed_user(uid="adm", email="adm@x.com", is_admin=True)
    login_fastapi(client, admin)
    bad = client.put(
        "/api/admin/ingest",
        json={"max_doc_chars": 100_000, "onyx_base_url": "http://internal.corp"},
    )
    assert bad.status_code == 400
    ok = client.put(
        "/api/admin/ingest",
        json={"max_doc_chars": 100_000, "onyx_base_url": ONYX},
    )
    assert ok.status_code == 200
    assert ok.json()["onyx_base_url"] == ONYX
    assert ingest_settings.get_onyx_base_url() == ONYX


def test_validate_onyx_base_url_rules():
    assert validate_onyx_base_url(ONYX) == ONYX
    assert validate_onyx_base_url("http://localhost:3000") == "http://localhost:3000"
    with pytest.raises(ValueError):
        validate_onyx_base_url("http://internal.host")
    with pytest.raises(ValueError):
        validate_onyx_base_url("https://onyx.example.com/")
    with pytest.raises(ValueError):
        validate_onyx_base_url("ftp://onyx.example.com")
    with pytest.raises(ValueError):
        validate_onyx_base_url("not-a-url")
