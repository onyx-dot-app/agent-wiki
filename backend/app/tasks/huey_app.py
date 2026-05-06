"""Huey instance backed by SQLite — separate file from app state.

We use SqliteHuey to avoid a Redis/Celery dependency. The app DB and queue DB
are intentionally separate so the worker has no contention with the Flask
process on FTS writes.
"""
from __future__ import annotations

from huey import SqliteHuey

from app.config import CONFIG

huey = SqliteHuey(filename=CONFIG.queue_db_path)
