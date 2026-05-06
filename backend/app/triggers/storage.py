"""Triggers are stored as YAML files in the wiki repo so they have history.

Layout: ``<wiki_dir>/.triggers/<trigger_id>.yaml``
We mirror them into the ``triggers`` SQLite table for fast scans.
"""
from __future__ import annotations

from pathlib import Path

from app.config import CONFIG

TRIGGERS_DIR = ".triggers"


def trigger_path(trigger_id: str) -> str:
    return f"{TRIGGERS_DIR}/{trigger_id}.yaml"


def ensure_triggers_dir() -> None:
    (Path(CONFIG.wiki_dir) / TRIGGERS_DIR).mkdir(parents=True, exist_ok=True)
