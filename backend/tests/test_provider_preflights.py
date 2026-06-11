"""Every provider preflight returns redacted diagnostics with ``ok`` gated on
the completion probe and a non-fatal listing probe."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import anthropic
import httpx
import ollama
import openai
import pytest

from app.llm.providers import anthropic as anthropic_provider
from app.llm.providers import gemini as gemini_provider
from app.llm.providers import ollama as ollama_provider
from app.llm.providers import openai as openai_provider
from app.llm.providers._common import PREFLIGHT_TIMEOUT_SECONDS
from app.llm.settings import LLMSettings


def _settings(**overrides: Any) -> LLMSettings:
    base: dict[str, Any] = {
        "provider": "openai",
        "model": "base-model",
        "anthropic_api_key": "sk-ant-test",
        "openai_api_key": "sk-openai-test",
        "gemini_api_key": "sk-gemini-test",
        "ollama_base_url": "http://localhost:11434",
        "custom_api_key": "",
        "custom_base_url": "",
        "custom_display_name": "",
        "provider_models": {},
        "ingest_selector_model": "",
    }
    base.update(overrides)
    return LLMSettings(**base)


class _FakeAnthropicClient:
    def __init__(self, models_exc: Exception | None, completion_exc: Exception | None):
        self._models_exc = models_exc
        self._completion_exc = completion_exc
        self.models = SimpleNamespace(list=self._list_models)
        self.messages = SimpleNamespace(create=self._create_message)
        self.completion_kwargs: dict[str, Any] = {}

    def _list_models(self) -> list[object]:
        if self._models_exc is not None:
            raise self._models_exc
        return []

    def _create_message(self, **kwargs: Any) -> object:
        self.completion_kwargs = kwargs
        if self._completion_exc is not None:
            raise self._completion_exc
        return object()


class _FakeOpenAIClient:
    def __init__(self, models_exc: Exception | None, completion_exc: Exception | None):
        self._models_exc = models_exc
        self._completion_exc = completion_exc
        self.models = SimpleNamespace(list=self._list_models)
        self.responses = SimpleNamespace(create=self._create_response)
        self.completion_kwargs: dict[str, Any] = {}

    def _list_models(self) -> list[object]:
        if self._models_exc is not None:
            raise self._models_exc
        return []

    def _create_response(self, **kwargs: Any) -> object:
        self.completion_kwargs = kwargs
        if self._completion_exc is not None:
            raise self._completion_exc
        return object()


class _FakeGeminiModels:
    def __init__(self, models_exc: Exception | None, completion_exc: Exception | None):
        self._models_exc = models_exc
        self._completion_exc = completion_exc
        self.completion_kwargs: dict[str, Any] = {}

    def list(self) -> list[object]:
        if self._models_exc is not None:
            raise self._models_exc
        return []

    def generate_content(self, **kwargs: Any) -> object:
        self.completion_kwargs = kwargs
        if self._completion_exc is not None:
            raise self._completion_exc
        return object()


class _FakeGeminiClient:
    def __init__(self, models_exc: Exception | None, completion_exc: Exception | None):
        self.models = _FakeGeminiModels(models_exc, completion_exc)


class _FakeOllamaClient:
    def __init__(self, models_exc: Exception | None, completion_exc: Exception | None):
        self._models_exc = models_exc
        self._completion_exc = completion_exc
        self.completion_kwargs: dict[str, Any] = {}

    def list(self) -> dict[str, Any]:
        if self._models_exc is not None:
            raise self._models_exc
        return {"models": []}

    def chat(self, **kwargs: Any) -> dict[str, Any]:
        self.completion_kwargs = kwargs
        if self._completion_exc is not None:
            raise self._completion_exc
        return {"message": {"content": "pong"}}


def _patch_anthropic_client(
    monkeypatch: pytest.MonkeyPatch,
    *,
    models_exc: Exception | None = None,
    completion_exc: Exception | None = None,
) -> tuple[_FakeAnthropicClient, dict[str, Any]]:
    fake = _FakeAnthropicClient(models_exc, completion_exc)
    ctor_kwargs: dict[str, Any] = {}

    def factory(**kwargs: Any) -> _FakeAnthropicClient:
        ctor_kwargs.update(kwargs)
        return fake

    monkeypatch.setattr(anthropic_provider, "Anthropic", factory)
    return fake, ctor_kwargs


def _patch_openai_client(
    monkeypatch: pytest.MonkeyPatch,
    *,
    models_exc: Exception | None = None,
    completion_exc: Exception | None = None,
) -> tuple[_FakeOpenAIClient, dict[str, Any]]:
    fake = _FakeOpenAIClient(models_exc, completion_exc)
    ctor_kwargs: dict[str, Any] = {}

    def factory(**kwargs: Any) -> _FakeOpenAIClient:
        ctor_kwargs.update(kwargs)
        return fake

    monkeypatch.setattr(openai_provider, "OpenAI", factory)
    return fake, ctor_kwargs


def _patch_gemini_client(
    monkeypatch: pytest.MonkeyPatch,
    *,
    models_exc: Exception | None = None,
    completion_exc: Exception | None = None,
) -> tuple[_FakeGeminiClient, dict[str, Any]]:
    fake = _FakeGeminiClient(models_exc, completion_exc)
    ctor_kwargs: dict[str, Any] = {}

    def factory(**kwargs: Any) -> _FakeGeminiClient:
        ctor_kwargs.update(kwargs)
        return fake

    monkeypatch.setattr(gemini_provider.genai, "Client", factory)
    return fake, ctor_kwargs


def _patch_ollama_client(
    monkeypatch: pytest.MonkeyPatch,
    *,
    models_exc: Exception | None = None,
    completion_exc: Exception | None = None,
) -> tuple[_FakeOllamaClient, dict[str, Any]]:
    fake = _FakeOllamaClient(models_exc, completion_exc)
    ctor_kwargs: dict[str, Any] = {}

    def factory(**kwargs: Any) -> _FakeOllamaClient:
        ctor_kwargs.update(kwargs)
        return fake

    monkeypatch.setattr(ollama_provider, "Client", factory)
    return fake, ctor_kwargs


def _openai_auth_error() -> openai.AuthenticationError:
    request = httpx.Request("POST", "https://api.openai.com/v1/responses")
    response = httpx.Response(status_code=401, request=request)
    return openai.AuthenticationError("bad key", response=response, body=None)


def _openai_not_found_error() -> openai.NotFoundError:
    request = httpx.Request("GET", "https://api.openai.com/v1/models")
    response = httpx.Response(status_code=404, request=request)
    return openai.NotFoundError("missing", response=response, body=None)


def _anthropic_auth_error() -> anthropic.AuthenticationError:
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    response = httpx.Response(status_code=401, request=request)
    return anthropic.AuthenticationError("bad key", response=response, body=None)


def _anthropic_not_found_error() -> anthropic.NotFoundError:
    request = httpx.Request("GET", "https://api.anthropic.com/v1/models")
    response = httpx.Response(status_code=404, request=request)
    return anthropic.NotFoundError("missing", response=response, body=None)


class _GeminiAuthError(Exception):
    def __init__(self) -> None:
        super().__init__("API_KEY_INVALID")
        self.code = 401


class _GeminiNotFoundError(Exception):
    def __init__(self) -> None:
        super().__init__("NotFound")
        self.code = 404


class ConnectError(Exception):
    pass


def _assert_redacted_shape(result: dict[str, Any], *, model: str) -> None:
    assert set(result) == {
        "ok",
        "base_url",
        "auth_present",
        "model",
        "models_endpoint",
        "completion",
    }
    assert result["model"] == model
    assert "api_key" not in result
    assert "sk-" not in str(result)


def test_anthropic_preflight_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    fake, ctor = _patch_anthropic_client(monkeypatch)

    result = anthropic_provider.PROVIDER.test_connection(_settings(), model="claude-3-5-haiku")

    _assert_redacted_shape(result, model="claude-3-5-haiku")
    assert result["ok"] is True
    assert result["base_url"] == ""
    assert result["auth_present"] is True
    assert result["models_endpoint"] == "ok"
    assert result["completion"] == "ok"
    assert fake.completion_kwargs["max_tokens"] == 1
    assert ctor["timeout"] == PREFLIGHT_TIMEOUT_SECONDS
    assert ctor["max_retries"] == 0


def test_anthropic_preflight_completion_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_anthropic_client(monkeypatch, completion_exc=_anthropic_auth_error())

    result = anthropic_provider.PROVIDER.test_connection(_settings(), model="claude-3-5-haiku")

    assert result["ok"] is False
    assert "rejected the API key" in result["completion"]


def test_anthropic_preflight_models_failure_is_nonfatal(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_anthropic_client(monkeypatch, models_exc=_anthropic_not_found_error())

    result = anthropic_provider.PROVIDER.test_connection(_settings(), model="claude-3-5-haiku")

    assert result["ok"] is True
    assert result["models_endpoint"] != "ok"
    assert result["completion"] == "ok"


def test_openai_preflight_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    fake, ctor = _patch_openai_client(monkeypatch)

    result = openai_provider.PROVIDER.test_connection(_settings(), model="gpt-4o-mini")

    _assert_redacted_shape(result, model="gpt-4o-mini")
    assert result["ok"] is True
    assert result["base_url"] == ""
    assert result["auth_present"] is True
    assert result["models_endpoint"] == "ok"
    assert result["completion"] == "ok"
    assert fake.completion_kwargs["max_output_tokens"] == 16
    assert ctor["timeout"] == PREFLIGHT_TIMEOUT_SECONDS
    assert ctor["max_retries"] == 0


def test_openai_preflight_completion_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_openai_client(monkeypatch, completion_exc=_openai_auth_error())

    result = openai_provider.PROVIDER.test_connection(_settings(), model="gpt-4o-mini")

    assert result["ok"] is False
    assert "rejected the API key" in result["completion"]


def test_openai_preflight_models_failure_is_nonfatal(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_openai_client(monkeypatch, models_exc=_openai_not_found_error())

    result = openai_provider.PROVIDER.test_connection(_settings(), model="gpt-4o-mini")

    assert result["ok"] is True
    assert result["models_endpoint"] != "ok"
    assert result["completion"] == "ok"


def test_gemini_preflight_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    fake, ctor = _patch_gemini_client(monkeypatch)

    result = gemini_provider.PROVIDER.test_connection(_settings(), model="gemini-2.5-flash")

    _assert_redacted_shape(result, model="gemini-2.5-flash")
    assert result["ok"] is True
    assert result["base_url"] == ""
    assert result["auth_present"] is True
    assert result["models_endpoint"] == "ok"
    assert result["completion"] == "ok"
    assert ctor == {"api_key": "sk-gemini-test"}
    assert fake.models.completion_kwargs["config"]["http_options"]["timeout"] == int(
        PREFLIGHT_TIMEOUT_SECONDS * 1000
    )


def test_gemini_preflight_completion_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_gemini_client(monkeypatch, completion_exc=_GeminiAuthError())

    result = gemini_provider.PROVIDER.test_connection(_settings(), model="gemini-2.5-flash")

    assert result["ok"] is False
    assert "rejected the API key" in result["completion"]


def test_gemini_preflight_models_failure_is_nonfatal(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_gemini_client(monkeypatch, models_exc=_GeminiNotFoundError())

    result = gemini_provider.PROVIDER.test_connection(_settings(), model="gemini-2.5-flash")

    assert result["ok"] is True
    assert result["models_endpoint"] != "ok"
    assert result["completion"] == "ok"


def test_ollama_preflight_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    fake, ctor = _patch_ollama_client(monkeypatch)

    result = ollama_provider.PROVIDER.test_connection(_settings(), model="llama3.1")

    _assert_redacted_shape(result, model="llama3.1")
    assert result["ok"] is True
    assert result["base_url"] == "http://localhost:11434"
    assert result["auth_present"] is False
    assert result["models_endpoint"] == "ok"
    assert result["completion"] == "ok"
    assert fake.completion_kwargs["options"] == {"num_predict": 1}
    assert ctor["host"] == "http://localhost:11434"
    assert ctor["timeout"] == PREFLIGHT_TIMEOUT_SECONDS


def test_ollama_preflight_completion_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_ollama_client(monkeypatch, completion_exc=ConnectError("Connection refused"))

    result = ollama_provider.PROVIDER.test_connection(_settings(), model="llama3.1")

    assert result["ok"] is False
    assert "Could not reach Ollama" in result["completion"]


def test_ollama_preflight_models_failure_is_nonfatal(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_ollama_client(monkeypatch, models_exc=ollama.ResponseError("missing", status_code=404))

    result = ollama_provider.PROVIDER.test_connection(_settings(), model="llama3.1")

    assert result["ok"] is True
    assert result["models_endpoint"] != "ok"
    assert result["completion"] == "ok"
