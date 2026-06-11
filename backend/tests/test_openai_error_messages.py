# Guard the invariant that OpenAI-labeled translated SDK errors stay byte-identical.
from __future__ import annotations

import httpx
import openai

from app.llm.providers._openai_errors import translate_openai_error


def _response(status_code: int) -> httpx.Response:
    request = httpx.Request("POST", "https://api.openai.com/v1/responses")
    return httpx.Response(status_code=status_code, request=request)


def _authentication_error(message: str) -> openai.AuthenticationError:
    return openai.AuthenticationError(
        message,
        response=_response(401),
        body={"message": message},
    )


def _rate_limit_error(message: str) -> openai.RateLimitError:
    return openai.RateLimitError(
        message,
        response=_response(429),
        body={"message": message},
    )


def _api_connection_error(message: str) -> openai.APIConnectionError:
    request = httpx.Request("POST", "https://api.openai.com/v1/responses")
    return openai.APIConnectionError(message=message, request=request)


def _not_found_error(message: str) -> openai.NotFoundError:
    return openai.NotFoundError(
        message,
        response=_response(404),
        body={"message": message},
    )


def test_translate_openai_authentication_message() -> None:
    err = translate_openai_error(
        _authentication_error("bad key"),
        provider_label="OpenAI",
    )

    assert err.code == "auth"
    assert (
        err.message == "OpenAI rejected the API key. An admin needs to update it on the admin page."
    )


def test_translate_openai_rate_limit_message() -> None:
    err = translate_openai_error(
        _rate_limit_error("slow down"),
        provider_label="OpenAI",
    )

    assert err.code == "rate_limit"
    assert err.message == "OpenAI rate limit hit. Please retry in a moment."


def test_translate_openai_connection_message() -> None:
    err = translate_openai_error(
        _api_connection_error("network down"),
        provider_label="OpenAI",
    )

    assert err.code == "network"
    assert err.message == "Could not reach OpenAI. Check the backend's network access."


def test_translate_openai_not_found_message() -> None:
    err = translate_openai_error(
        _not_found_error("missing model"),
        provider_label="OpenAI",
    )

    assert err.code == "config"
    assert (
        err.message
        == "OpenAI returned 'not found' — usually a bad model name. Check the configured model."
    )
