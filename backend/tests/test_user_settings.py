"""Tests for /api/user/settings — per-user preferences."""
from __future__ import annotations

import pytest

from app.auth import users as users_repo
from app.models.user_settings import UserSettings


# --------------------------------------------------------------------------- #
# Repo                                                                        #
# --------------------------------------------------------------------------- #


def test_get_settings_returns_defaults_for_fresh_user(tmp_db):
    from tests._seed import seed_user

    seed_user(uid="usr_a", email="a@x.com")
    s = users_repo.get_settings("usr_a")
    assert s is not None
    assert s == UserSettings().model_dump()
    assert s["theme"] == "system"
    assert s["timezone"] == "UTC"
    assert s["default_landing"] == "wiki_home"


def test_get_settings_returns_none_for_missing_user(tmp_db):
    assert users_repo.get_settings("nope") is None


def test_update_settings_persists_partial(tmp_db):
    from tests._seed import seed_user

    seed_user(uid="usr_a", email="a@x.com")
    out = users_repo.update_settings("usr_a", {"theme": "dark"})
    assert out is not None
    assert out["theme"] == "dark"
    # Other fields keep defaults.
    assert out["timezone"] == "UTC"

    # Persisted across reads.
    again = users_repo.get_settings("usr_a")
    assert again is not None
    assert again["theme"] == "dark"


def test_update_settings_merges_over_existing(tmp_db):
    from tests._seed import seed_user

    seed_user(uid="usr_a", email="a@x.com")
    users_repo.update_settings("usr_a", {"theme": "dark", "timezone": "America/New_York"})
    users_repo.update_settings("usr_a", {"default_landing": "recent"})

    out = users_repo.get_settings("usr_a")
    assert out is not None
    assert out["theme"] == "dark"
    assert out["timezone"] == "America/New_York"
    assert out["default_landing"] == "recent"


def test_update_settings_returns_none_for_missing_user(tmp_db):
    assert users_repo.update_settings("nope", {"theme": "dark"}) is None


# --------------------------------------------------------------------------- #
# Pydantic validation                                                         #
# --------------------------------------------------------------------------- #


def test_user_settings_rejects_unknown_theme():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        UserSettings.model_validate({"theme": "purple"})


def test_user_settings_rejects_invalid_timezone():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        UserSettings.model_validate({"timezone": "Mars/Olympus_Mons"})


def test_user_settings_accepts_iana_timezone():
    s = UserSettings.model_validate({"timezone": "America/Los_Angeles"})
    assert s.timezone == "America/Los_Angeles"


def test_user_settings_ignores_unknown_keys():
    """Stale JSON from an older app version shouldn't break login."""
    s = UserSettings.model_validate({"theme": "dark", "obsolete_key": True})
    assert s.theme == "dark"


# --------------------------------------------------------------------------- #
# HTTP                                                                        #
# --------------------------------------------------------------------------- #


@pytest.fixture
def client(tmp_repo):
    from app.main import create_app

    app = create_app()
    app.config["TESTING"] = True
    c = app.test_client()
    resp = c.post(
        "/api/auth/signup",
        json={"email": "t@example.com", "password": "hunter22", "name": "t"},
    )
    assert resp.status_code == 201, resp.get_data(as_text=True)
    return c


def test_get_settings_returns_defaults(client):
    resp = client.get("/api/user/settings")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["theme"] == "system"
    assert body["timezone"] == "UTC"
    assert body["default_landing"] == "wiki_home"
    assert "density" not in body
    assert "show_agent_reasoning" not in body


def test_put_settings_persists_partial(client):
    resp = client.put("/api/user/settings", json={"theme": "dark"})
    assert resp.status_code == 200, resp.get_data(as_text=True)
    body = resp.get_json()
    assert body["theme"] == "dark"
    assert body["timezone"] == "UTC"  # untouched

    # Round-trip via GET.
    again = client.get("/api/user/settings").get_json()
    assert again["theme"] == "dark"


def test_put_settings_rejects_bad_enum(client):
    resp = client.put("/api/user/settings", json={"theme": "purple"})
    assert resp.status_code == 400


def test_put_settings_rejects_bad_timezone(client):
    resp = client.put("/api/user/settings", json={"timezone": "Mars/Olympus_Mons"})
    assert resp.status_code == 400


def test_put_settings_rejects_unknown_field(client):
    resp = client.put("/api/user/settings", json={"obsolete_key": "x"})
    assert resp.status_code == 400


def test_settings_endpoints_require_auth(tmp_repo):
    from app.main import create_app

    app = create_app()
    app.config["TESTING"] = True
    c = app.test_client()
    assert c.get("/api/user/settings").status_code == 401
    assert c.put("/api/user/settings", json={"theme": "dark"}).status_code == 401


def test_auth_me_payload_includes_settings(client):
    # Update first so we know the persisted shape comes through.
    client.put(
        "/api/user/settings",
        json={"theme": "dark", "default_landing": "recent"},
    )
    resp = client.get("/api/auth/me")
    assert resp.status_code == 200
    body = resp.get_json()
    assert "settings" in body
    assert body["settings"]["theme"] == "dark"
    assert body["settings"]["default_landing"] == "recent"
    assert body["settings"]["timezone"] == "UTC"
