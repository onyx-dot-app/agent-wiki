# Shared provider-seam error mapping for OpenAI-SDK providers; keep the SDK import inside providers/.
from __future__ import annotations

import openai

from app.llm.errors import LLMError


def translate_openai_error(exc: Exception, *, provider_label: str) -> LLMError:
    if isinstance(exc, openai.AuthenticationError):
        return LLMError(
            "auth",
            f"{provider_label} rejected the API key. An admin needs to update it on the admin page.",
        )
    if isinstance(exc, openai.PermissionDeniedError):
        return LLMError(
            "auth",
            f"{provider_label} denied access for the configured API key.",
        )
    if isinstance(exc, openai.RateLimitError):
        return LLMError(
            "rate_limit",
            f"{provider_label} rate limit hit. Please retry in a moment.",
        )
    if isinstance(exc, openai.APIConnectionError):
        return LLMError(
            "network",
            f"Could not reach {provider_label}. Check the backend's network access.",
        )
    if isinstance(exc, openai.NotFoundError):
        return LLMError(
            "config",
            f"{provider_label} returned 'not found' — usually a bad model name. Check the configured model.",
        )
    if isinstance(exc, openai.BadRequestError):
        return LLMError("bad_request", f"{provider_label} rejected the request: {exc}")
    if isinstance(exc, openai.APIStatusError):
        return LLMError("provider", f"{provider_label} error: {exc}")
    return LLMError("unknown", f"Unexpected error talking to {provider_label}.")
