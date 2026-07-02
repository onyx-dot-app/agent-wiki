"""Centralized logging configuration.

Call ``setup_logging()`` once per process at startup (Flask app factory,
``app.tasks.run_worker`` entry point, scripts). Module code uses the
standard ``logging.getLogger(__name__)`` pattern; the root configuration
set here gives every logger consistent formatting and level handling.

Format: ``<ts> [<level>] <logger> (<file>:<line>): <message>``

Level is read from the ``LOG_LEVEL`` env var (default ``INFO``). Valid
values: ``DEBUG``, ``INFO``, ``WARNING``, ``ERROR``, ``CRITICAL``.
"""
from __future__ import annotations

import logging
import os
import sys

_FORMAT = "%(asctime)s [%(levelname)s] %(name)s (%(filename)s:%(lineno)d): %(message)s"
_DATEFMT = "%Y-%m-%d %H:%M:%S"

_configured = False


def setup_logging(level: str | int | None = None) -> None:
    """Configure the root logger. Idempotent — safe to call more than once."""
    global _configured

    resolved = level if level is not None else os.environ.get("LOG_LEVEL", "INFO")
    if isinstance(resolved, str):
        resolved = resolved.upper()

    root = logging.getLogger()
    root.setLevel(resolved)

    if _configured:
        # Already wired — just update the level so a second call with a new
        # LOG_LEVEL takes effect without duplicating handlers.
        return

    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter(_FORMAT, datefmt=_DATEFMT))
    root.addHandler(handler)

    # Tame noisy third-party loggers; flip to DEBUG via env if needed.
    logging.getLogger("werkzeug").setLevel(logging.INFO)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)
    # botocore DEBUG logs full request headers (bearer tokens included) and
    # every stream chunk — never allow it below INFO.
    logging.getLogger("botocore").setLevel(logging.INFO)
    logging.getLogger("boto3").setLevel(logging.INFO)
    logging.getLogger("urllib3").setLevel(logging.INFO)

    _configured = True


def get_logger(name: str) -> logging.Logger:
    """Convenience wrapper. Equivalent to ``logging.getLogger(name)``."""
    return logging.getLogger(name)
