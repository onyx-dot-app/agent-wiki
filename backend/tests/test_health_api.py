"""Tests for ``/api/health``.

Asserts the shape of the response and that trailing-slash variants
both 200 — the route registers both forms so clients with sloppy
URLs don't 307-redirect chase.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import create_app


@pytest.fixture
def client(tmp_db):
    return TestClient(create_app())


def test_health_returns_200(client):
    res = client.get("/api/health")
    assert res.status_code == 200


def test_health_trailing_slash_also_200(client):
    res = client.get("/api/health/")
    assert res.status_code == 200


def test_health_response_shape(client):
    body = client.get("/api/health").json()
    assert body["status"] in ("ok", "degraded")
    assert isinstance(body["queues"], list)
    assert len(body["queues"]) > 0
    for q in body["queues"]:
        assert {"name", "ready", "delayed", "in_flight", "limit", "ok", "error"} <= set(q)
