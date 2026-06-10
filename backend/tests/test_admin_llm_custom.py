"""Custom OpenAI-compatible provider settings: admin PUT/GET semantics
(redaction, keep-vs-clear, base_url validation) and the user-facing
/llm/status + /llm/available surfaces."""

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


def _put(client, **body):
    return client.put("/api/admin/llm", json=body)


def _configure_custom(client, **overrides):
    body = {
        "provider": "custom",
        "model": "deepseek-chat",
        "custom_api_key": "sk-custom-secret-12345",
        "custom_base_url": "https://gw.example.com/v1",
        "custom_display_name": "DeepSeek",
        "provider_models": {"custom": ["deepseek-chat"]},
    }
    body.update(overrides)
    return _put(client, **body)


def test_put_custom_persists_and_redacts(client, admin):
    r = _configure_custom(client)
    assert r.status_code == 200
    body = r.json()
    assert body["provider"] == "custom"
    assert body["custom_api_key_set"] is True
    assert body["custom_base_url"] == "https://gw.example.com/v1"
    assert body["custom_display_name"] == "DeepSeek"
    assert body["provider_models"]["custom"] == ["deepseek-chat"]

    got = client.get("/api/admin/llm").json()
    assert "sk-custom-secret-12345" not in str(got)
    assert got["custom_api_key_hint"].startswith("sk-c")
    assert "…" in got["custom_api_key_hint"]


def test_custom_is_allowed_provider(client, admin):
    r = _put(client, provider="custom", model="m")
    assert r.status_code == 200


def test_base_url_scheme_validated(client, admin):
    r = _put(client, custom_base_url="gw.example.com/v1")
    assert r.status_code == 400
    assert "http" in r.json()["error"]


def test_base_url_chat_completions_suffix_rejected(client, admin):
    r = _put(client, custom_base_url="https://gw.example.com/v1/chat/completions")
    assert r.status_code == 400


def test_base_url_trailing_slash_stripped(client, admin):
    r = _put(client, custom_base_url="https://gw.example.com/v1/")
    assert r.status_code == 200
    assert r.json()["custom_base_url"] == "https://gw.example.com/v1"


def test_key_empty_string_keeps_null_clears_absent_keeps(client, admin):
    _configure_custom(client)

    # Absent field → keep.
    r = _put(client, custom_display_name="renamed")
    assert r.json()["custom_api_key_set"] is True

    # Empty string → keep.
    r = _put(client, custom_api_key="")
    assert r.json()["custom_api_key_set"] is True

    # Explicit null → clear.
    r = _put(client, custom_api_key=None)
    assert r.json()["custom_api_key_set"] is False


def test_display_name_empty_string_clears(client, admin):
    _configure_custom(client)
    r = _put(client, custom_display_name="")
    assert r.json()["custom_display_name"] == ""


def test_status_configured_for_custom(client, admin):
    _configure_custom(client)
    body = client.get("/api/llm/status").json()
    assert body["configured"] is True
    assert body["provider"] == "custom"


def test_available_includes_custom_with_label_and_models(client, admin):
    _configure_custom(client, provider_models={"custom": ["deepseek-chat", "deepseek-reasoner"]})
    providers = client.get("/api/llm/available").json()["providers"]
    custom = next(p for p in providers if p["provider"] == "custom")
    assert custom["label"] == "DeepSeek"
    assert custom["models"] == ["deepseek-chat", "deepseek-reasoner"]
    assert custom["default_model"] == "deepseek-chat"


def test_available_label_falls_back_to_custom(client, admin):
    _configure_custom(client, custom_display_name="")
    providers = client.get("/api/llm/available").json()["providers"]
    custom = next(p for p in providers if p["provider"] == "custom")
    assert custom["label"] == "Custom"


def test_available_omits_custom_without_models(client, admin):
    _configure_custom(client, provider_models={"custom": []})
    providers = client.get("/api/llm/available").json()["providers"]
    assert all(p["provider"] != "custom" for p in providers)
