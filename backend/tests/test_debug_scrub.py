"""DEBUG LLM-payload dumps scrub secrets before logging (app/llm/client._debug_dump)."""
from __future__ import annotations

import logging

import pytest

from app.llm import client


def test_scrubs_keyed_secret_fields() -> None:
    out = client._scrub_secrets('{"anthropic_api_key": "sk-ant-abc123def456ghi789"}')
    assert "sk-ant-abc123def456ghi789" not in out
    assert '"anthropic_api_key": "[redacted]"' in out


def test_scrubs_known_token_shapes_anywhere() -> None:
    # A key pasted into free-text message content, not under a secret-y key.
    out = client._scrub_secrets('{"content": "my key is sk-ant-abc123def456ghi789xyz please use it"}')
    assert "abc123def456ghi789xyz" not in out
    assert "sk-ant-[redacted]" in out


def test_scrubs_bearer_and_aws() -> None:
    assert "[redacted]" in client._scrub_secrets("Authorization: Bearer abcdef1234567890")
    assert "AKIA[redacted]" in client._scrub_secrets("AKIAIOSFODNN7EXAMPLE")


def test_leaves_ordinary_content_and_numbers_untouched() -> None:
    payload = '{"role": "user", "content": "summarize the runbook", "input_tokens": 512}'
    assert client._scrub_secrets(payload) == payload


def test_debug_dump_logs_scrubbed(caplog: pytest.LogCaptureFixture) -> None:
    obj = {"messages": [{"role": "user", "content": "token: sk-ant-SECRETSECRETSECRET123"}]}
    with caplog.at_level(logging.DEBUG, logger="app.llm.client"):
        client._debug_dump("llm request messages", obj)
    logged = "\n".join(r.getMessage() for r in caplog.records)
    assert "llm request messages" in logged
    assert "SECRETSECRETSECRET123" not in logged
    assert "sk-ant-[redacted]" in logged
