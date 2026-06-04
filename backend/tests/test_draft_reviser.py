"""Tests for the draft reviser agent + POST /api/wiki/revise."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.auth import users as users_repo
from app.llm.agents import draft_reviser
from app.main import create_app
from tests._auth import login_fastapi


def _llm_response(text: str) -> MagicMock:
    m = MagicMock(text=text)
    m.usage.input_tokens = 10
    m.usage.output_tokens = 20
    return m


@patch("app.llm.agents.draft_reviser.client")
def test_revise_returns_trimmed_body(mock_client: MagicMock) -> None:
    mock_client.complete.return_value = _llm_response("# New Title\n\nbody\n")
    assert draft_reviser.revise("# Old\n", "rename it") == "# New Title\n\nbody"


@patch("app.llm.agents.draft_reviser.client")
def test_revise_passes_body_and_instruction(mock_client: MagicMock) -> None:
    mock_client.complete.return_value = _llm_response("out")
    draft_reviser.revise("# Doc\n\ncontent", "make it formal")
    sent = mock_client.complete.call_args.args[0]
    user_turn = sent[-1]["content"]
    assert "# Doc" in user_turn and "make it formal" in user_turn


@pytest.fixture
def client(tmp_db: None, tmp_repo: None) -> TestClient:
    return TestClient(create_app())


def test_revise_endpoint_unauthenticated_is_401(client: TestClient) -> None:
    resp = client.post("/api/wiki/revise", json={"body": "x", "instruction": "y"})
    assert resp.status_code == 401


@patch("app.llm.agents.draft_reviser.client")
def test_revise_endpoint_returns_body(mock_client: MagicMock, client: TestClient) -> None:
    mock_client.complete.return_value = _llm_response("# Revised\n\nnew\n")
    uid = users_repo.create(email="nik@x.com", password="hunter2-x", name="Nik")
    login_fastapi(client, uid)

    resp = client.post(
        "/api/wiki/revise",
        json={"body": "# Orig\n", "instruction": "rewrite"},
    )

    assert resp.status_code == 200
    assert resp.json()["body"] == "# Revised\n\nnew"


def test_revise_endpoint_422_on_empty_instruction(client: TestClient) -> None:
    uid = users_repo.create(email="nik@x.com", password="hunter2-x", name="Nik")
    login_fastapi(client, uid)
    resp = client.post("/api/wiki/revise", json={"body": "x", "instruction": ""})
    assert resp.status_code == 422
