"""Best-effort secret scrubbing for DEBUG log payloads.

Shared by every place that dumps full LLM payloads at ``LOG_LEVEL=DEBUG``
(``app/llm/client.py`` and ``app/llm/agents/chat.py``) — the message history,
tool definitions, tool-call arguments, and tool results, any of which can carry
a secret (a key pasted into a prompt, a tool result holding a config value or
HTTP headers). Centralised here so the two debug-dump paths can't drift.

This is pattern matching, not a guarantee — it catches secret-keyed JSON fields
and well-known credential token shapes, so DEBUG stays an operator-only level.
"""
from __future__ import annotations

import re

_REDACTED = "[redacted]"

# Values under a secret-ish JSON key — "api_key": "...", "x-api-key": "...".
# Matches the rendered JSON, so it catches secrets nested anywhere. The
# surrounding class allows ``-`` so hyphenated header-style keys (x-api-key,
# access-token) are covered. Numbers (e.g. "input_tokens": 5) are unquoted and
# don't match.
_KEYED_SECRET_RE = re.compile(
    r'("[a-z_-]*(?:api[_-]?key|secret|token|password|authorization)[a-z_-]*"\s*:\s*")[^"]+(")',
    re.IGNORECASE,
)
# Recognizable credential token shapes — conservative, prefix-anchored so we
# don't mangle ordinary prose. Keep the prefix for debuggability, redact the body.
_TOKEN_RES = [
    re.compile(r"(sk-ant-)[A-Za-z0-9_\-]{12,}"),  # Anthropic
    re.compile(r"(sk-(?:proj-)?)[A-Za-z0-9_\-]{16,}"),  # OpenAI
    re.compile(r"(AIza)[A-Za-z0-9_\-]{20,}"),  # Google / Gemini
    re.compile(r"(AKIA)[0-9A-Z]{16}"),  # AWS access key id
    re.compile(r"(xox[baprs]-)[A-Za-z0-9-]{8,}"),  # Slack
    re.compile(r"(gh[pousr]_)[A-Za-z0-9]{20,}"),  # GitHub
    re.compile(r"(Bearer\s+)[A-Za-z0-9._\-]{8,}", re.IGNORECASE),  # bearer tokens
]


def scrub_secrets(text: str) -> str:
    """Mask credentials in a rendered debug payload before it hits the log."""
    text = _KEYED_SECRET_RE.sub(rf"\1{_REDACTED}\2", text)
    for pat in _TOKEN_RES:
        text = pat.sub(rf"\1{_REDACTED}", text)
    return text
