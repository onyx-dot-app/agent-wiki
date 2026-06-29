"""Custom OpenAI-compatible provider settings: admin PUT/GET semantics
(redaction, keep-vs-clear, base_url validation) and the user-facing
/llm/status + /llm/available surfaces."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.llm import providers as llm_providers
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


def test_allowlist_matches_registry() -> None:
    assert set(llm_providers.names()) == {
        "anthropic",
        "openai",
        "gemini",
        "ollama",
        "custom",
        "bedrock",
    }


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


def test_preflight_unconfigured_is_400(client, admin):
    r = client.post("/api/admin/llm/custom/test", json={})
    assert r.status_code == 400
    assert "base URL" in r.json()["error"]


def test_preflight_without_any_model_is_400(client, admin):
    _put(client, custom_base_url="https://gw.example.com/v1")
    r = client.post("/api/admin/llm/custom/test", json={})
    assert r.status_code == 400
    assert "model" in r.json()["error"]


def test_preflight_falls_back_to_first_saved_model(client, admin, monkeypatch):
    _configure_custom(client)
    seen: dict[str, str] = {}

    def fake_test(settings, *, model):
        seen["model"] = model
        return {
            "ok": True,
            "base_url": settings.custom_base_url,
            "auth_present": True,
            "model": model,
            "models_endpoint": "ok",
            "completion": "ok",
        }

    provider = llm_providers.get("custom")
    assert provider is not None
    monkeypatch.setattr(provider, "test_connection", fake_test)
    r = client.post("/api/admin/llm/custom/test", json={})
    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert seen["model"] == "deepseek-chat"


def test_preflight_explicit_model_wins(client, admin, monkeypatch):
    _configure_custom(client)
    seen: dict[str, str] = {}

    def fake_test(settings, *, model):
        seen["model"] = model
        return {
            "ok": False,
            "base_url": settings.custom_base_url,
            "auth_present": True,
            "model": model,
            "models_endpoint": "ok",
            "completion": "Custom provider rejected the API key.",
        }

    provider = llm_providers.get("custom")
    assert provider is not None
    monkeypatch.setattr(provider, "test_connection", fake_test)
    r = client.post("/api/admin/llm/custom/test", json={"model": "other-model"})
    assert r.status_code == 200
    assert r.json()["ok"] is False
    assert seen["model"] == "other-model"


def test_preflight_unknown_provider_is_404(client, admin):
    r = client.post("/api/admin/llm/custom-bogus/test", json={})
    assert r.status_code == 404
    assert "unknown provider" in r.json()["error"]


def test_preflight_builtin_provider_dispatches_via_registry(client, admin, monkeypatch):
    _put(
        client,
        provider="anthropic",
        model="claude-3-5-haiku-latest",
        anthropic_api_key="sk-ant-test",
        provider_models={"anthropic": ["claude-3-5-haiku-latest"]},
    )
    seen: dict[str, str] = {}

    def fake_test(settings, *, model):
        seen["model"] = model
        return {
            "ok": True,
            "base_url": "",
            "auth_present": bool(settings.anthropic_api_key),
            "model": model,
            "models_endpoint": "ok",
            "completion": "ok",
        }

    provider = llm_providers.get("anthropic")
    assert provider is not None
    monkeypatch.setattr(provider, "test_connection", fake_test)

    r = client.post("/api/admin/llm/anthropic/test", json={})

    assert r.status_code == 200
    assert r.json()["ok"] is True
    assert seen["model"] == "claude-3-5-haiku-latest"
