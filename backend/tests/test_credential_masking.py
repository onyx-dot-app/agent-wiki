"""Credential masking: hints must not reconstruct short secrets, and the
ingest API key is show-once — reads return only set/hint, never the raw key."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import create_app

from tests._auth import login_fastapi
from tests._seed import seed_user


@pytest.fixture
def client(tmp_db):
    return TestClient(create_app())


@pytest.fixture
def admin(client):
    uid = seed_user(uid="usr_adm", email="admin@x.com", is_admin=True)
    login_fastapi(client, uid)
    return uid


def test_short_key_hint_is_fixed_width(client, admin):
    short = "sk-12345"  # 8 chars
    medium = "sk-123456789"  # 12 chars — first4+last4 would expose 8/12
    client.put("/api/admin/llm", json={"custom_api_key": medium})
    got = client.get("/api/admin/llm").json()
    assert got["custom_api_key_hint"] == "••••••••"
    assert medium not in str(got)
    assert short not in str(got)


def test_long_key_hint_shows_edges_only(client, admin):
    key = "sk-or-v1-" + "a" * 40
    client.put("/api/admin/llm", json={"custom_api_key": key})
    got = client.get("/api/admin/llm").json()
    assert got["custom_api_key_hint"] == f"{key[:4]}…{key[-4:]}"
    assert key not in str(got)


def test_ingest_get_never_returns_raw_key(client, admin):
    raw = client.post("/api/admin/ingest/regenerate-key").json()["api_key"]
    assert raw  # show-once response carries the key

    got = client.get("/api/admin/ingest").json()
    assert "api_key" not in got
    assert got["api_key_set"] is True
    assert raw not in str(got)
    assert got["api_key_hint"] != ""


def test_ingest_get_before_any_key(client, admin):
    got = client.get("/api/admin/ingest").json()
    assert got["api_key_set"] is False
    assert got["api_key_hint"] == ""
