"""Provider-agnostic LLM error type.

Lives in its own module so provider implementations can raise/translate
``LLMError`` without importing ``app.llm.client`` (which imports the
provider registry, which imports the providers — a cycle).
"""
from __future__ import annotations


class LLMError(Exception):
    """User-presentable LLM failure.

    ``code`` is a short, stable string the API layer maps to an HTTP status
    (e.g. ``"not_configured"`` → 503, ``"auth"`` → 502, ``"rate_limit"`` →
    429). ``message`` is safe to show the user — keep it short, don't
    include keys, secrets, or full SDK stack traces.
    """

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message
