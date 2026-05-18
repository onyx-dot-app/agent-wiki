"""Launchers API surface — catalog, launch, exchange, probes."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.db import launch_codes as codes_repo
from app.auth import mcp_tokens as tokens_repo
from app.db.session import init_db
from app.db import agent_sessions as sessions_repo
from app.main import create_app

from tests._auth import login_fastapi
from tests._seed import seed_user


def _get_session_dict(sid):
    from app.db import agent_sessions as _sr

    row = _sr.get(sid)
    assert row is not None
    return row


@pytest.fixture
def client(tmp_config):
    init_db()
    return TestClient(create_app())


# --------------------------------------------------------------------------- #
# Catalog                                                                     #
# --------------------------------------------------------------------------- #


def test_get_catalog_requires_auth(client):
    res = client.get("/api/launchers")
    assert res.status_code in (401, 403)


def test_get_catalog_returns_all_manifests(client):
    uid = seed_user()
    login_fastapi(client, uid)
    res = client.get("/api/launchers")
    assert res.status_code == 200
    ids = {x["id"] for x in res.json()["launchers"]}
    assert ids == {"claude-code", "codex", "onyx-craft"}


def test_setup_status_token_false_when_no_tokens(client):
    uid = seed_user()
    login_fastapi(client, uid)
    res = client.get("/api/launchers")
    for entry in res.json()["launchers"]:
        assert entry["setup_status"]["token"] is False


def test_setup_status_token_true_after_mint(client):
    uid = seed_user()
    login_fastapi(client, uid)
    tokens_repo.create(uid, "k")
    res = client.get("/api/launchers")
    for entry in res.json()["launchers"]:
        assert entry["setup_status"]["token"] is True


def test_catalog_available_for_launch_flag(client):
    """— only local_cli tools are available_for_launch in v1."""
    uid = seed_user()
    login_fastapi(client, uid)
    res = client.get("/api/launchers")
    by_id = {x["id"]: x for x in res.json()["launchers"]}
    assert by_id["claude-code"]["available_for_launch"] is True
    assert by_id["codex"]["available_for_launch"] is True
    assert by_id["onyx-craft"]["available_for_launch"] is False


def test_catalog_with_machine_id_includes_default_workdir(client):
    """— when probe pipeline supplies machine_id + wiki_path, the
    response includes the stored working-dir default."""
    from app.db import page_dirs

    uid = seed_user()
    login_fastapi(client, uid)
    page_dirs.set_for_page(
        user_id=uid,
        machine_id="m_a",
        wiki_path="docs/x.md",
        working_dir="/home/u/proj",
    )
    res = client.get("/api/launchers?machine_id=m_a&wiki_path=docs%2Fx.md")
    for entry in res.json()["launchers"]:
        assert entry["default_working_dir"] == "/home/u/proj"


def test_catalog_normalizes_wiki_path_for_default_lookup(client):
    from app.db import page_dirs

    uid = seed_user()
    login_fastapi(client, uid)
    page_dirs.set_for_page(
        user_id=uid,
        machine_id="m_norm",
        wiki_path="docs/x.md",
        working_dir="/workspace",
    )
    res = client.get(
        "/api/launchers",
        params={"machine_id": "m_norm", "wiki_path": "./docs//x.md"},
    )
    assert res.status_code == 200, res.text
    for entry in res.json()["launchers"]:
        assert entry["default_working_dir"] == "/workspace"


# --------------------------------------------------------------------------- #
# POST /api/launch                                                            #
# --------------------------------------------------------------------------- #


def test_post_launch_creates_session_and_returns_uri(client):
    uid = seed_user()
    login_fastapi(client, uid)
    res = client.post(
        "/api/launch",
        json={"tool_id": "claude-code", "wiki_path": None, "message": "do the thing"},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["agent_session_id"].startswith("as_")
    assert body["launch_code"].startswith("lc_")
    assert body["uri"].startswith("agentwiki://run?")
    assert f"code={body['launch_code']}" in body["uri"]
    assert "tool=claude-code" in body["uri"]


def test_post_launch_auto_mints_token_when_none_exists(client):
    uid = seed_user()
    login_fastapi(client, uid)
    assert tokens_repo.list_for_user(uid) == []
    res = client.post(
        "/api/launch",
        json={"tool_id": "claude-code", "wiki_path": None, "message": "x"},
    )
    assert res.status_code == 200, res.text
    rows = tokens_repo.list_for_user(uid)
    assert len(rows) == 1


def test_post_launch_unknown_tool_returns_404(client):
    uid = seed_user()
    login_fastapi(client, uid)
    res = client.post(
        "/api/launch",
        json={"tool_id": "does-not-exist", "wiki_path": None, "message": "x"},
    )
    assert res.status_code == 404


def test_post_launch_in_app_kind_returns_400(client):
    """in_app routes through a separate endpoint ; 400 here."""
    uid = seed_user()
    login_fastapi(client, uid)
    res = client.post(
        "/api/launch",
        json={"tool_id": "onyx-craft", "wiki_path": None, "message": "x"},
    )
    assert res.status_code == 400


def test_post_launch_rejects_traversal(client):
    """— wiki_path traversal rejected at API boundary."""
    uid = seed_user()
    login_fastapi(client, uid)
    res = client.post(
        "/api/launch",
        json={
            "tool_id": "claude-code",
            "wiki_path": "../../etc/passwd",
            "message": "x",
        },
    )
    assert res.status_code == 400


def test_post_launch_message_length_capped(client):
    """— message capped at 16KB."""
    uid = seed_user()
    login_fastapi(client, uid)
    huge = "x" * (16 * 1024 + 1)
    res = client.post(
        "/api/launch",
        json={"tool_id": "claude-code", "wiki_path": None, "message": huge},
    )
    # App's error handler translates pydantic ValidationError to 400.
    assert res.status_code in (400, 422)


def test_post_launch_normalizes_wiki_path(client):
    uid = seed_user()
    login_fastapi(client, uid)
    res = client.post(
        "/api/launch",
        json={
            "tool_id": "claude-code",
            "wiki_path": "./docs//example.md",
            "message": "x",
        },
    )
    assert res.status_code == 200, res.text
    row = _get_session_dict(res.json()["agent_session_id"])
    assert row["wiki_path"] == "docs/example.md"


def test_post_launch_resume_rejects_active_session(client):
    """— concurrent resume race protection."""
    uid = seed_user()
    login_fastapi(client, uid)
    sid = sessions_repo.create(
        user_id=uid,
        tool_id="claude-code",
        first_turn_prompt="x",
        wiki_path=None,
        working_dir=None,
    )
    sessions_repo.mark_active(sid, machine_id="m")
    res = client.post(
        "/api/launch",
        json={
            "tool_id": "claude-code",
            "wiki_path": None,
            "message": "x",
            "resume_session_id": sid,
        },
    )
    assert res.status_code == 409


def test_post_launch_resume_rejects_tool_mismatch(client):
    uid = seed_user()
    login_fastapi(client, uid)
    sid = sessions_repo.create(
        user_id=uid,
        tool_id="claude-code",
        first_turn_prompt="x",
        wiki_path=None,
        working_dir=None,
    )
    sessions_repo.close(sid, reason="user")
    res = client.post(
        "/api/launch",
        json={
            "tool_id": "codex",
            "wiki_path": None,
            "message": "resume",
            "resume_session_id": sid,
        },
    )
    assert res.status_code == 400


def test_post_launch_resume_copies_cli_and_machine(client):
    """Resume launch should thread CLI session + machine gate through new session."""
    uid = seed_user()
    login_fastapi(client, uid)
    sid = sessions_repo.create(
        user_id=uid,
        tool_id="claude-code",
        first_turn_prompt="x",
        wiki_path=None,
        working_dir=None,
    )
    sessions_repo.mark_active(sid, machine_id="m_laptop")
    sessions_repo.set_cli_session_id(sid, "cli_resume")
    sessions_repo.close(sid, reason="user")

    res = client.post(
        "/api/launch",
        json={
            "tool_id": "claude-code",
            "wiki_path": None,
            "message": "resume please",
            "resume_session_id": sid,
        },
    )
    assert res.status_code == 200, res.text
    body = res.json()
    new_sid = body["agent_session_id"]
    assert new_sid != sid
    row = sessions_repo.get(new_sid)
    assert row is not None
    assert row["machine_id"] == "m_laptop"
    assert row["cli_session_id"] == "cli_resume"

    fresh = _fresh_client()
    exchange = fresh.post(
        "/api/launch/exchange",
        json={"code": body["launch_code"], "machine_id": "m_laptop"},
    )
    assert exchange.status_code == 200, exchange.text
    payload = exchange.json()["payload"]
    assert payload["session_id"] == new_sid
    assert payload["cli_session_id"] == "cli_resume"
    assert payload["first_turn_prompt"] is None


# --------------------------------------------------------------------------- #
# Exchange                                                                    #
# --------------------------------------------------------------------------- #


def _fresh_client():
    return TestClient(create_app())


def test_exchange_consumes_code_and_returns_token(client):
    uid = seed_user()
    login_fastapi(client, uid)
    launch_res = client.post(
        "/api/launch",
        json={"tool_id": "claude-code", "wiki_path": None, "message": "x"},
    )
    code = launch_res.json()["launch_code"]
    fresh = _fresh_client()
    res = fresh.post(
        "/api/launch/exchange",
        json={"code": code, "machine_id": "m_abc"},
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["mcp_token"].startswith("mcp_")
    assert body["manifest"]["id"] == "claude-code"
    assert body["payload"]["first_turn_prompt"] is not None
    assert body["payload"]["session_id"].startswith("as_")


def test_exchange_unknown_code_returns_404(client):
    fresh = _fresh_client()
    res = fresh.post(
        "/api/launch/exchange",
        json={"code": "lc_nope", "machine_id": "m"},
    )
    assert res.status_code == 404


def test_exchange_consumed_code_returns_409(client):
    uid = seed_user()
    login_fastapi(client, uid)
    code = client.post(
        "/api/launch",
        json={"tool_id": "claude-code", "wiki_path": None, "message": "x"},
    ).json()["launch_code"]
    fresh = _fresh_client()
    fresh.post("/api/launch/exchange", json={"code": code, "machine_id": "m"})
    res = fresh.post("/api/launch/exchange", json={"code": code, "machine_id": "m"})
    assert res.status_code == 409


def test_exchange_expired_code_returns_410(client, monkeypatch):
    monkeypatch.setattr(
        codes_repo,
        "CONFIG",
        codes_repo.CONFIG.model_copy(update={"launch_code_ttl_seconds": 0}),
    )
    uid = seed_user()
    login_fastapi(client, uid)
    code = client.post(
        "/api/launch",
        json={"tool_id": "claude-code", "wiki_path": None, "message": "x"},
    ).json()["launch_code"]
    fresh = _fresh_client()
    res = fresh.post("/api/launch/exchange", json={"code": code, "machine_id": "m"})
    assert res.status_code == 410


def test_exchange_transitions_session_to_active_with_machine_id(client):
    uid = seed_user()
    login_fastapi(client, uid)
    launch_body = client.post(
        "/api/launch",
        json={"tool_id": "claude-code", "wiki_path": None, "message": "x"},
    ).json()
    fresh = _fresh_client()
    fresh.post(
        "/api/launch/exchange",
        json={"code": launch_body["launch_code"], "machine_id": "m_xyz"},
    )
    row = sessions_repo.get(launch_body["agent_session_id"])
    assert row is not None
    assert row["status"] == "active"
    assert row["machine_id"] == "m_xyz"


def test_exchange_rejects_when_session_closed_before_exchange(client):
    """Closing the session before helper exchange must block reuse."""
    uid = seed_user()
    login_fastapi(client, uid)
    launch_body = client.post(
        "/api/launch",
        json={"tool_id": "claude-code", "wiki_path": None, "message": "x"},
    ).json()
    sid = launch_body["agent_session_id"]
    code = launch_body["launch_code"]

    close = client.post(
        f"/api/agent-sessions/{sid}/close",
        json={"reason": "user_clicked"},
    )
    assert close.status_code == 204, close.text

    fresh = _fresh_client()
    res = fresh.post(
        "/api/launch/exchange",
        json={"code": code, "machine_id": "m_after_close"},
    )
    assert res.status_code == 409, res.text

    row = sessions_repo.get(sid)
    assert row is not None
    assert row["status"] == "closed"


def test_exchange_rejects_machine_id_mismatch_on_resume(client):
    """— exchange refuses if helper's machine_id differs from session's."""
    uid = seed_user()
    login_fastapi(client, uid)
    sid = sessions_repo.create(
        user_id=uid,
        tool_id="claude-code",
        first_turn_prompt="x",
        wiki_path=None,
        working_dir=None,
    )
    sessions_repo.mark_active(sid, machine_id="m_original")
    # Force close so we can issue a resume launch.
    sessions_repo.close(sid, reason="user")
    from app.db import launcher_tokens as lt_repo

    tid, _ = lt_repo.get_or_mint_for_user(uid, name="launcher-claude-code")
    sessions_repo.set_cli_session_id(sid, "cli_xyz")
    # Mint a launch code targeting that session, then exchange with a
    # different machine_id.
    code = codes_repo.create(user_id=uid, agent_session_id=sid, mcp_token_id=tid)
    fresh = _fresh_client()
    res = fresh.post(
        "/api/launch/exchange",
        json={"code": code, "machine_id": "m_different"},
    )
    assert res.status_code == 409


# --------------------------------------------------------------------------- #
# Probe                                                                       #
# --------------------------------------------------------------------------- #


def test_probe_ack_then_status(client):
    uid = seed_user()
    login_fastapi(client, uid)
    nonce = "test_nonce_123"
    client.post(
        "/api/launch/probe-ack",
        json={"nonce": nonce, "helper_port": 31415, "machine_id": "m_abc"},
    )
    res = client.get(f"/api/launch/probe-status?nonce={nonce}")
    assert res.status_code == 200
    body = res.json()
    assert body["acked"] is True
    assert body["helper_port"] == 31415
    assert body["machine_id"] == "m_abc"


def test_probe_status_no_ack_returns_acked_false(client):
    uid = seed_user()
    login_fastapi(client, uid)
    res = client.get("/api/launch/probe-status?nonce=never_acked")
    assert res.status_code == 200
    assert res.json()["acked"] is False


def test_probe_status_requires_auth(client):
    res = client.get("/api/launch/probe-status?nonce=anything")
    assert res.status_code in (401, 403)
