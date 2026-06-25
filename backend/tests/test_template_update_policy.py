"""A template can carry a default update policy that is applied to a page
created from it (app/wiki/templates.py + put_document_by_path seeding)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.auth import users as users_repo
from app.main import create_app
from app.wiki import templates as templates_repo
from app.wiki import update_policy
from tests._auth import login_fastapi


@pytest.fixture
def client(tmp_db: None, tmp_repo: None) -> TestClient:
    return TestClient(create_app())


def _make_template(uid: str, **policy: object) -> dict:
    return templates_repo.create(
        name="Meeting notes",
        body="# Notes\n",
        description=None,
        system_prompt=None,
        created_by_user_id=uid,
        **policy,
    )


def test_template_round_trips_policy_fields(tmp_db: None) -> None:
    t = _make_template(
        None,
        ingestion_auto_update_disabled=True,
        update_instruction="Only meeting facts.",
    )
    got = templates_repo.get(t["id"])
    assert got is not None
    assert got["ingestion_auto_update_disabled"] is True
    assert got["update_instruction"] == "Only meeting facts."


def test_page_created_from_template_inherits_policy(client: TestClient) -> None:
    uid = users_repo.create(email="o@x.com", password="hunter2-x", name="O")
    login_fastapi(client, uid)
    t = _make_template(
        uid,
        ingestion_auto_update_disabled=True,
        update_instruction="Only meeting facts.",
    )
    path = "team/notes.md"
    # The editor records the page is being written from this template...
    assert (
        client.post(
            "/api/wiki/file/draft", json={"path": path, "template_id": t["id"]}
        ).status_code
        == 200
    )
    # ...then the first save creates the page.
    assert (
        client.put(
            "/api/wiki/file", json={"path": path, "body": "# Notes\n\nfacts\n"}
        ).status_code
        == 200
    )

    eff = update_policy.resolve_for_path(path)
    assert eff.ingestion_auto_update_disabled is True
    assert eff.update_instruction == "Only meeting facts."


def test_template_without_policy_seeds_nothing(client: TestClient) -> None:
    uid = users_repo.create(email="o@x.com", password="hunter2-x", name="O")
    login_fastapi(client, uid)
    t = _make_template(uid)  # no policy fields
    path = "team/plain.md"
    client.post("/api/wiki/file/draft", json={"path": path, "template_id": t["id"]})
    client.put("/api/wiki/file", json={"path": path, "body": "# Plain\n\nx\n"})

    # No explicit policy row → inherited defaults (auto-update on, no instruction).
    assert update_policy.get(path) is None
    eff = update_policy.resolve_for_path(path)
    assert eff.ingestion_auto_update_disabled is False
    assert eff.update_instruction is None
