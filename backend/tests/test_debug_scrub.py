"""DEBUG LLM-payload dumps scrub secrets before logging.

Covers the shared scrubber (app/llm/redact.scrub_secrets) and both debug-dump
paths that use it: app/llm/client and app/llm/agents/chat.
"""
from __future__ import annotations

import logging

import pytest

from app.llm import client
from app.llm.agents import chat
from app.llm.redact import scrub_secrets


def test_scrubs_keyed_secret_fields() -> None:
    out = scrub_secrets('{"anthropic_api_key": "sk-ant-abc123def456ghi789"}')
    assert "sk-ant-abc123def456ghi789" not in out
    assert '"anthropic_api_key": "[redacted]"' in out


def test_scrubs_hyphenated_header_style_keys() -> None:
    # Hyphenated keys (HTTP headers serialized into a tool result) must be covered.
    out = scrub_secrets('{"x-api-key": "raw-secret-value", "access-token": "another-one"}')
    assert "raw-secret-value" not in out
    assert "another-one" not in out
    assert '"x-api-key": "[redacted]"' in out
    assert '"access-token": "[redacted]"' in out


def test_scrubs_known_token_shapes_anywhere() -> None:
    # A key pasted into free-text message content, not under a secret-y key.
    out = scrub_secrets('{"content": "my key is sk-ant-abc123def456ghi789xyz please use it"}')
    assert "abc123def456ghi789xyz" not in out
    assert "sk-ant-[redacted]" in out


def test_scrubs_bearer_and_aws() -> None:
    assert "[redacted]" in scrub_secrets("Authorization: Bearer abcdef1234567890")
    assert "AKIA[redacted]" in scrub_secrets("AKIAIOSFODNN7EXAMPLE")


def test_leaves_ordinary_content_and_numbers_untouched() -> None:
    payload = '{"role": "user", "content": "summarize the runbook", "input_tokens": 512}'
    assert scrub_secrets(payload) == payload


def test_client_debug_dump_logs_scrubbed(caplog: pytest.LogCaptureFixture) -> None:
    obj = {"messages": [{"role": "user", "content": "token: sk-ant-SECRETSECRETSECRET123"}]}
    with caplog.at_level(logging.DEBUG, logger="app.llm.client"):
        client._debug_dump("llm request messages", obj)
    logged = "\n".join(r.getMessage() for r in caplog.records)
    assert "llm request messages" in logged
    assert "SECRETSECRETSECRET123" not in logged
    assert "sk-ant-[redacted]" in logged


def test_chat_debug_dump_scrubs_tool_results(caplog: pytest.LogCaptureFixture) -> None:
    # Tool-call args/results flow through chat._debug_dump — the path the
    # original PR missed. Model a tool result that serializes HTTP response
    # headers (nested dict, the case the reviewer flagged).
    tool_result = {"role": "tool", "content": {"headers": {"x-api-key": "secret-header-value-123"}}}
    with caplog.at_level(logging.DEBUG, logger="app.llm.agents.chat"):
        chat._debug_dump("chat tool result", tool_result)
    logged = "\n".join(r.getMessage() for r in caplog.records)
    assert "secret-header-value-123" not in logged
    assert '"x-api-key": "[redacted]"' in logged
