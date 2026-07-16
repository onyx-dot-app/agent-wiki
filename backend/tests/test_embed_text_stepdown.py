"""embed_text step-down retry on the embeddings token limit.

The char caps bound characters, not tokens, so a capped body can still exceed
the model's 8192-token limit. embed_text must shrink-and-retry until it fits
rather than drop the vector — while leaving non-token errors as a plain None.
"""

from __future__ import annotations

import pytest

from app.llm import embeddings


_TOKEN_ERR = "Invalid 'input[0]': maximum input length is 8192 tokens."


@pytest.fixture(autouse=True)
def _key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(embeddings, "_api_key", lambda: "sk-test")
    monkeypatch.setattr(embeddings, "model_name", lambda: "text-embedding-3-small")


def test_stepdown_retries_until_it_fits(monkeypatch: pytest.MonkeyPatch):
    """Fails at >20k chars (over token limit), succeeds once truncated."""
    calls: list[int] = []

    def fake_embed(key: str, model: str, inputs: list[str]) -> list[list[float]]:
        n = len(inputs[0])
        calls.append(n)
        if n > 20_000:
            raise RuntimeError(_TOKEN_ERR)
        return [[0.1] * embeddings.EMBED_DIM]

    monkeypatch.setattr(embeddings.openai_provider, "embed", fake_embed)

    vec = embeddings.embed_text("x" * 24_000)

    assert vec is not None and len(vec) == embeddings.EMBED_DIM
    # 24000 (fail) -> 20000 (ok): stepped down by _STEP_DOWN_CHARS once.
    assert calls == [24_000, 20_000]


def test_non_token_error_returns_none_without_retry(monkeypatch: pytest.MonkeyPatch):
    calls: list[int] = []

    def fake_embed(key: str, model: str, inputs: list[str]) -> list[list[float]]:
        calls.append(len(inputs[0]))
        raise RuntimeError("Connection reset by peer")

    monkeypatch.setattr(embeddings.openai_provider, "embed", fake_embed)

    assert embeddings.embed_text("x" * 24_000) is None
    assert calls == [24_000]  # no shrink-and-retry on a non-token error


def test_gives_up_at_floor(monkeypatch: pytest.MonkeyPatch):
    """Pathological content that overruns even at the floor: bounded, returns None."""
    calls: list[int] = []

    def fake_embed(key: str, model: str, inputs: list[str]) -> list[list[float]]:
        calls.append(len(inputs[0]))
        raise RuntimeError(_TOKEN_ERR)  # always too long

    monkeypatch.setattr(embeddings.openai_provider, "embed", fake_embed)

    assert embeddings.embed_text("x" * 24_000) is None
    # Steps 24k -> 20k -> ... -> 4k (floor), then gives up. Bounded, ends at floor.
    assert calls[0] == 24_000 and calls[-1] == embeddings._MIN_EMBED_CHARS
    assert all(a > b for a, b in zip(calls, calls[1:]))  # strictly decreasing


def test_no_key_returns_none(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(embeddings, "_api_key", lambda: "")
    assert embeddings.embed_text("hello") is None
