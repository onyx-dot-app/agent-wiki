"""Tests for the auto-update health knobs on the admin ingest settings endpoint
(GET/PUT /api/admin/ingest)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.auth import users as users_repo
from app.ingest import settings as ingest_settings
from app.main import create_app
from tests._auth import login_fastapi


@pytest.fixture
def client(tmp_db: None, tmp_repo: None) -> TestClient:
    return TestClient(create_app())


def test_defaults_exposed(client: TestClient) -> None:
    admin = users_repo.create(email="admin@x.com", password="hunter2-x", name="A")
    login_fastapi(client, admin)  # first user is auto-admin
    got = client.get("/api/admin/ingest").json()
    assert got["warn_update_threshold_default"] == 10
    assert got["auto_update_cap"] == 200


def test_put_round_trips_health_knobs(client: TestClient) -> None:
    admin = users_repo.create(email="admin@x.com", password="hunter2-x", name="A")
    login_fastapi(client, admin)
    put = client.put(
        "/api/admin/ingest",
        json={
            "max_doc_chars": 100000,
            "warn_update_threshold_default": 5,
            "auto_update_cap": 25,
        },
    )
    assert put.status_code == 200
    assert put.json()["warn_update_threshold_default"] == 5
    assert put.json()["auto_update_cap"] == 25
    # Persisted.
    assert ingest_settings.get().auto_update_cap == 25


def test_requires_admin(client: TestClient) -> None:
    users_repo.create(email="admin@x.com", password="hunter2-x", name="A")  # admin #1
    member = users_repo.create(email="m@x.com", password="hunter2-x", name="M")
    login_fastapi(client, member)
    assert client.get("/api/admin/ingest").status_code == 403
