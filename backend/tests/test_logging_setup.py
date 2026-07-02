"""setup_logging keeps AWS/HTTP client loggers at INFO even under a DEBUG
root, so botocore can never dump request auth headers into the logs."""
from __future__ import annotations

import logging

from app.utils.logging import setup_logging


def test_third_party_loggers_capped_under_debug():
    setup_logging(level="DEBUG")
    assert logging.getLogger().level == logging.DEBUG
    for name in ("botocore", "boto3", "urllib3"):
        assert logging.getLogger(name).getEffectiveLevel() >= logging.INFO
