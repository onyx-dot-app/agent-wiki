"""verify_secret_key(): the production guard against the default/empty SECRET_KEY.

SECRET_KEY signs session cookies and derives the at-rest encryption key, so the
public built-in default must be fatal on a real deployment (DEV_MODE off) and a
warning in local dev / CI (DEV_MODE on).
"""
from __future__ import annotations

import logging

import pytest

from app.config import CONFIG, DEV_SECRET_KEY, Config, verify_secret_key


def _cfg(*, secret_key: str, dev_mode: bool) -> Config:
    return CONFIG.model_copy(update={"secret_key": secret_key, "dev_mode": dev_mode})


def test_rejects_default_key_in_production() -> None:
    with pytest.raises(ValueError, match="SECRET_KEY"):
        verify_secret_key(_cfg(secret_key=DEV_SECRET_KEY, dev_mode=False))


def test_rejects_empty_key_in_production() -> None:
    with pytest.raises(ValueError, match="SECRET_KEY"):
        verify_secret_key(_cfg(secret_key="", dev_mode=False))


def test_warns_but_allows_default_key_in_dev(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        verify_secret_key(_cfg(secret_key=DEV_SECRET_KEY, dev_mode=True))
    assert any("SECRET_KEY" in r.message for r in caplog.records)


def test_warns_but_allows_empty_key_in_dev(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING):
        verify_secret_key(_cfg(secret_key="", dev_mode=True))
    assert any("SECRET_KEY" in r.message for r in caplog.records)


def test_accepts_real_key_in_production() -> None:
    # A configured key boots cleanly regardless of dev_mode and emits no warning.
    verify_secret_key(_cfg(secret_key="a-real-32-byte-hex-secret", dev_mode=False))
