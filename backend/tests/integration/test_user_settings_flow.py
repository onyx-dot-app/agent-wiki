"""End-to-end persistence flow for /api/user/settings.

The unit tests in ``tests/test_user_settings.py`` exercise the repo +
HTTP layer in isolation. These tests prove the full chain: settings
written via the API land in the ``users.settings`` JSONB column, are
read back by ``/auth/me`` on the next session, and survive a fresh
``create_app()`` boot (the closest proxy for a process restart in a
test).
"""
from __future__ import annotations

import pytest


def test_settings_persist_across_logout_and_login(integration):
    """User updates settings, logs out, logs back in — settings stick.

    The same Flask app instance is reused but the session cookie is
    cleared between, so the second /auth/me has to round-trip through
    the DB to recover the saved values.
    """
    integration.signup(email="u@x.com", password="hunter2-x")

    # Defaults right after signup.
    me = integration.client.get("/api/auth/me").json()
    assert me["settings"]["theme"] == "system"
    assert me["settings"]["timezone"] is None
    assert me["settings"]["default_landing"] == "wiki_home"

    # Update each of the three fields.
    resp = integration.client.put(
        "/api/user/settings",
        json={
            "theme": "dark",
            "timezone": "America/Los_Angeles",
            "default_landing": "last_viewed",
        },
    )
    assert resp.status_code == 200, resp.text

    # Logout drops the session cookie.
    integration.client.post("/api/auth/logout")
    assert integration.client.get("/api/auth/me").status_code == 401

    # Sign back in (same email, new session) — settings come from the DB.
    resp = integration.client.post(
        "/api/auth/login", json={"email": "u@x.com", "password": "hunter2-x"}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["settings"]["theme"] == "dark"
    assert body["settings"]["timezone"] == "America/Los_Angeles"
    assert body["settings"]["default_landing"] == "last_viewed"

    # /auth/me on the new session also reflects the persisted values.
    me = integration.client.get("/api/auth/me").json()
    assert me["settings"]["theme"] == "dark"
    assert me["settings"]["timezone"] == "America/Los_Angeles"
    assert me["settings"]["default_landing"] == "last_viewed"


def test_settings_persist_across_fresh_app_boot(tmp_repo):
    """A fresh ``create_app()`` reads the same DB state — proves
    persistence isn't memoized in the app instance.

    Bypasses the ``integration`` fixture so we can spin up a second
    app pointed at the same per-test schema.
    """
    from fastapi.testclient import TestClient

    from app.main import create_app

    c1 = TestClient(create_app())

    # Signup + write settings on app1.
    resp = c1.post(
        "/api/auth/signup",
        json={"email": "p@x.com", "password": "hunter2-x", "name": "P"},
    )
    assert resp.status_code == 201, resp.text

    resp = c1.put(
        "/api/user/settings",
        json={
            "theme": "light",
            "timezone": "Europe/Berlin",
            "default_landing": "recent",
        },
    )
    assert resp.status_code == 200

    # Simulate a process restart — fresh app, fresh client (the new
    # client gets its own empty session, so app1's cookie is irrelevant).
    c2 = TestClient(create_app())

    # Login on the new app instance, verify the saved settings come back.
    resp = c2.post(
        "/api/auth/login", json={"email": "p@x.com", "password": "hunter2-x"}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["settings"]["theme"] == "light"
    assert body["settings"]["timezone"] == "Europe/Berlin"
    assert body["settings"]["default_landing"] == "recent"


def test_settings_partial_update_preserves_other_fields(integration):
    """Sequential partial PUTs each persist independently — no field
    gets clobbered by an unrelated update on the next request.
    """
    integration.signup(email="u@x.com", password="hunter2-x")

    integration.client.put("/api/user/settings", json={"theme": "dark"})
    integration.client.put("/api/user/settings", json={"timezone": "Asia/Tokyo"})
    integration.client.put(
        "/api/user/settings", json={"default_landing": "recent"}
    )

    me = integration.client.get("/api/auth/me").json()
    assert me["settings"]["theme"] == "dark"
    assert me["settings"]["timezone"] == "Asia/Tokyo"
    assert me["settings"]["default_landing"] == "recent"


def test_settings_landing_in_db_jsonb_column(integration):
    """Belt-and-braces: assert directly on ``users.settings`` JSONB
    that the column is what we put in, not just what the API echoes.
    """
    user_id = integration.signup(email="u@x.com", password="hunter2-x")

    integration.client.put(
        "/api/user/settings",
        json={"theme": "dark", "timezone": "America/New_York"},
    )

    from sqlalchemy import select

    from app.db.models import User
    from app.db.session import session

    with session() as s:
        row = s.scalar(select(User).where(User.id == user_id))
        assert row is not None
        assert row.settings["theme"] == "dark"
        assert row.settings["timezone"] == "America/New_York"


def test_settings_isolated_between_users(integration):
    """One user's settings don't bleed into another's."""
    integration.signup(email="alice@x.com", password="hunter2-x")
    integration.client.put(
        "/api/user/settings",
        json={"theme": "dark", "timezone": "America/Los_Angeles"},
    )
    integration.client.post("/api/auth/logout")

    integration.signup(email="bob@x.com", password="hunter2-x")
    me = integration.client.get("/api/auth/me").json()
    # Bob is a new account — should see defaults, not Alice's values.
    assert me["settings"]["theme"] == "system"
    assert me["settings"]["timezone"] is None

    # And Bob's own update doesn't touch Alice's row.
    integration.client.put("/api/user/settings", json={"theme": "light"})
    integration.client.post("/api/auth/logout")

    resp = integration.client.post(
        "/api/auth/login",
        json={"email": "alice@x.com", "password": "hunter2-x"},
    )
    assert resp.status_code == 200
    assert resp.json()["settings"]["theme"] == "dark"


def test_invalid_settings_rejected_and_db_unchanged(integration):
    """A 400 response from PUT must not leave half-applied state in
    the JSONB column. Validation runs against the merged shape, so a
    partial that fails should roll back the whole write.
    """
    user_id = integration.signup(email="u@x.com", password="hunter2-x")

    # Set a known-good baseline.
    integration.client.put(
        "/api/user/settings",
        json={"theme": "dark", "timezone": "America/Los_Angeles"},
    )

    # Send a partial that's valid for one field, broken for another —
    # the model validator on the merged shape rejects unknown timezone.
    resp = integration.client.put(
        "/api/user/settings",
        json={"theme": "light", "timezone": "Mars/Olympus_Mons"},
    )
    assert resp.status_code == 400

    # DB still holds the previous good values — neither field flipped.
    from sqlalchemy import select

    from app.db.models import User
    from app.db.session import session

    with session() as s:
        row = s.scalar(select(User).where(User.id == user_id))
        assert row is not None
        assert row.settings["theme"] == "dark"
        assert row.settings["timezone"] == "America/Los_Angeles"


@pytest.mark.parametrize(
    "field, valid_value",
    [
        ("theme", "dark"),
        ("timezone", "Australia/Sydney"),
        ("default_landing", "recent"),
    ],
)
def test_each_field_round_trips_independently(integration, field, valid_value):
    """Every field's PUT → GET round-trip works on its own."""
    integration.signup(email="u@x.com", password="hunter2-x")
    resp = integration.client.put("/api/user/settings", json={field: valid_value})
    assert resp.status_code == 200
    me = integration.client.get("/api/auth/me").json()
    assert me["settings"][field] == valid_value
