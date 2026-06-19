"""POST /api/craft/launch + the craft_launch worker task."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.db import agent_sessions as sessions_repo
from app.db import notifications as notifications_repo
from app.db.session import init_db
from app.ingest import settings as ingest_settings
from app.main import create_app
from app.onyx import connections
from app.onyx.client import (
    OnyxAuthError,
    OnyxCapacityError,
    OnyxError,
    OnyxUnreachableError,
)
from app.tasks.craft import attachment_filename
from app.tasks.queues import craft_queue
from app.wiki import acl as wiki_acl
from app.wiki import git as wiki_git

from tests._auth import login_fastapi
from tests._seed import seed_user

ONYX = "https://onyx.example.com"
PAGE = "Projects/Page One.md"


@pytest.fixture
def client(tmp_config):
    init_db()
    return TestClient(create_app())


@pytest.fixture
def connected_user(client) -> str:
    ingest_settings.upsert(max_doc_chars=100_000, onyx_base_url=ONYX)
    uid = seed_user()
    login_fastapi(client, uid)
    connections.upsert(
        user_id=uid,
        onyx_pat="onyx_pat_" + "p" * 40,
        onyx_user_email="nik@onyx.app",
        expires_at=None,
        onyx_base_url=ONYX,
    )
    return uid


def _fake_client(
    monkeypatch: pytest.MonkeyPatch,
    *,
    message_count: int = 0,
    create_error: OnyxError | None = None,
) -> list[tuple[str, Any]]:
    """Patch the worker's OnyxClient seam; returns the recorded call list."""
    calls: list[tuple[str, Any]] = []

    class Fake:
        def __init__(self, base_url: str, pat: str):
            calls.append(("init", base_url))

        def create_build_session(self) -> str:
            if create_error is not None:
                raise create_error
            calls.append(("create", None))
            return "bs_123"

        def set_session_name(self, session_id: str, *, name: str) -> None:
            calls.append(("name", (session_id, name)))

        def upload_attachment(self, session_id: str, *, filename: str, content: bytes) -> None:
            calls.append(("upload", (session_id, filename)))

        def session_message_count(self, session_id: str) -> int:
            calls.append(("count", session_id))
            return message_count

        def send_seed_message(self, session_id: str, *, content: str) -> None:
            calls.append(("send", (session_id, content)))

    monkeypatch.setattr("app.tasks.craft.OnyxClient", Fake)
    return calls


def _launch(client, *, wiki_path: str | None = PAGE, message: str = "build a dashboard"):
    return client.post("/api/craft/launch", json={"wiki_path": wiki_path, "message": message})


# --------------------------------------------------------------------------- #
# Gates                                                                       #
# --------------------------------------------------------------------------- #


def test_launch_requires_connection(client):
    ingest_settings.upsert(max_doc_chars=100_000, onyx_base_url=ONYX)
    uid = seed_user()
    login_fastapi(client, uid)
    res = _launch(client, wiki_path=None)
    assert res.status_code == 409
    assert res.json()["error"] == "needs_onyx_connect"


def test_launch_acl_denied(client, connected_user):
    wiki_git.commit_file("secret/plan.md", "# secret\n", "seed", author=None)
    other = seed_user(uid="other", email="o@x.com")
    wiki_acl.set_owner("secret/plan.md", other)
    res = _launch(client, wiki_path="secret/plan.md")
    assert res.status_code == 403
    assert sessions_repo.list_for_user(connected_user) == []


def test_launch_rate_limited_per_user(client, connected_user):
    for i in range(3):
        sessions_repo.create(
            user_id=connected_user,
            tool_id="onyx-craft",
            first_turn_prompt="x",
            wiki_path=f"p{i}.md",
            working_dir=None,
            status="provisioning",
        )
    res = _launch(client, wiki_path=None)
    assert res.status_code == 429


# --------------------------------------------------------------------------- #
# Happy path + idempotency                                                    #
# --------------------------------------------------------------------------- #


def test_launch_happy_path(client, connected_user, monkeypatch):
    wiki_git.commit_file(PAGE, "# Page One\nbody\n", "seed", author=None)
    calls = _fake_client(monkeypatch)
    with craft_queue.immediate_mode():
        res = _launch(client)
    assert res.status_code == 200
    sid = res.json()["agent_session_id"]

    row = sessions_repo.get(sid)
    assert row is not None
    assert row["status"] == "ready"
    assert row["external_session_id"] == "bs_123"
    assert row["external_url"] == f"{ONYX}/craft/v1?sessionId=bs_123"

    ops = [c[0] for c in calls]
    assert ops == ["init", "create", "name", "upload", "count", "send"]
    assert calls[2][1] == ("bs_123", "Page One")
    upload_args = calls[3][1]
    assert upload_args == ("bs_123", "Page_One.md")
    seed_content = calls[5][1][1]
    assert "PAGE_ATTACHMENT: attachments/Page_One.md" in seed_content
    assert "build a dashboard" in seed_content

    page = notifications_repo.list_for_user(connected_user)
    assert page["undismissed_count"] == 1
    notif = page["notifications"][0]
    assert notif["notif_type"] == "craft_ready"
    assert notif["data"]["link"] == f"{ONYX}/craft/v1?sessionId=bs_123"
    assert notif["data"]["agent_session_id"] == sid


def test_launch_idempotent_for_same_page(client, connected_user, monkeypatch):
    wiki_git.commit_file(PAGE, "# Page One\n", "seed", author=None)
    calls = _fake_client(monkeypatch)
    with craft_queue.immediate_mode():
        first = _launch(client)
        second = _launch(client)
    assert first.json()["agent_session_id"] == second.json()["agent_session_id"]
    assert [c[0] for c in calls].count("create") == 1


def test_seed_send_skipped_when_session_has_messages(client, connected_user, monkeypatch):
    wiki_git.commit_file(PAGE, "# Page One\n", "seed", author=None)
    calls = _fake_client(monkeypatch, message_count=1)
    with craft_queue.immediate_mode():
        res = _launch(client)
    assert res.status_code == 200
    assert "send" not in [c[0] for c in calls]
    row = sessions_repo.get(res.json()["agent_session_id"])
    assert row is not None
    assert row["status"] == "ready"


# --------------------------------------------------------------------------- #
# Failure taxonomy                                                            #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("error", "reason", "connection_survives"),
    [
        (OnyxAuthError("401"), "auth_expired", False),
        (OnyxCapacityError("429"), "org_at_capacity", True),
        (OnyxUnreachableError("down"), "onyx_unreachable", True),
        (OnyxError("weird"), "provisioning_failed", True),
    ],
)
def test_launch_failure_taxonomy(
    client, connected_user, monkeypatch, error, reason, connection_survives
):
    wiki_git.commit_file(PAGE, "# Page One\n", "seed", author=None)
    _fake_client(monkeypatch, create_error=error)
    with craft_queue.immediate_mode():
        res = _launch(client)
    assert res.status_code == 200  # the launch itself succeeded; the task failed

    row = sessions_repo.get(res.json()["agent_session_id"])
    assert row is not None
    assert row["status"] == "failed"
    assert row["failure_reason"] == reason
    assert (connections.status(connected_user) is not None) == connection_survives

    page = notifications_repo.list_for_user(connected_user)
    notif = page["notifications"][0]
    assert notif["notif_type"] == "craft_failed"
    assert notif["data"]["failure_reason"] == reason


# --------------------------------------------------------------------------- #
# attachment_filename                                                         #
# --------------------------------------------------------------------------- #


def test_attachment_filename_sanitizes():
    assert attachment_filename("Engineering Projects/Craft Integration.md") == (
        "Craft_Integration.md"
    )
    assert attachment_filename("a/b/c.md") == "c.md"
    assert attachment_filename("weird*page?.md").endswith(".md")
    assert "/" not in attachment_filename("x/../../etc/passwd.md")
    assert attachment_filename("....md") == "page.md"
