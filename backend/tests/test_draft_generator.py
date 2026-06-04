"""Tests for the AI draft generator agent + POST /api/wiki/generate."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.auth import users as users_repo
from app.llm.agents import draft_generator
from app.main import create_app
from tests._auth import login_fastapi


def _llm_response(text: str) -> MagicMock:
    m = MagicMock(text=text)
    m.usage.input_tokens = 10
    m.usage.output_tokens = 20
    return m


# --------------------------------------------------------------------------- #
# draft_generator.generate                                                     #
# --------------------------------------------------------------------------- #


@patch("app.llm.agents.draft_generator.client")
def test_generate_extracts_title_from_heading(mock_client: MagicMock) -> None:
    mock_client.complete.return_value = _llm_response("# My Plan\n\nintro\n")
    out = draft_generator.generate("plan something")
    assert out["title"] == "My Plan"
    assert out["body"] == "# My Plan\n\nintro"


@patch("app.llm.agents.draft_generator.client")
def test_generate_falls_back_to_first_line(mock_client: MagicMock) -> None:
    mock_client.complete.return_value = _llm_response("Just a line\nmore\n")
    assert draft_generator.generate("x")["title"] == "Just a line"


@patch("app.llm.agents.draft_generator.client")
def test_generate_untitled_when_empty(mock_client: MagicMock) -> None:
    mock_client.complete.return_value = _llm_response("   \n\n")
    out = draft_generator.generate("x")
    assert out["title"] == "Untitled"
    assert out["body"] == ""


# --------------------------------------------------------------------------- #
# POST /api/wiki/generate                                                      #
# --------------------------------------------------------------------------- #


@pytest.fixture
def client(tmp_db: None, tmp_repo: None) -> TestClient:
    return TestClient(create_app())


def test_generate_endpoint_unauthenticated_is_401(client: TestClient) -> None:
    assert client.post("/api/wiki/generate", json={"prompt": "hi"}).status_code == 401


@patch("app.llm.agents.draft_generator.client")
def test_generate_endpoint_returns_draft(mock_client: MagicMock, client: TestClient) -> None:
    mock_client.complete.return_value = _llm_response("# Title\n\nbody text\n")
    uid = users_repo.create(email="nik@x.com", password="hunter2-x", name="Nik")
    login_fastapi(client, uid)

    resp = client.post("/api/wiki/generate", json={"prompt": "write about cats"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["title"] == "Title"
    assert body["body"] == "# Title\n\nbody text"


def test_generate_endpoint_422_on_empty_prompt(client: TestClient) -> None:
    uid = users_repo.create(email="nik@x.com", password="hunter2-x", name="Nik")
    login_fastapi(client, uid)
    assert client.post("/api/wiki/generate", json={"prompt": ""}).status_code == 422
