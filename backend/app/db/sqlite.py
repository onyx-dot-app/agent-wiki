"""SQLite connection helper.

App state lives in ``app.sqlite``. The Huey queue lives in a separate
``queue.sqlite`` so workers can be deployed independently of the web tier.
"""
from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from app.config import CONFIG

_MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def connect() -> sqlite3.Connection:
    Path(CONFIG.app_db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(CONFIG.app_db_path, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


@contextmanager
def cursor() -> Iterator[sqlite3.Cursor]:
    conn = connect()
    try:
        yield conn.cursor()
    finally:
        conn.close()


def init_db() -> None:
    """Apply all migrations in lexical order. Idempotent."""
    if not _MIGRATIONS_DIR.exists():
        return
    with cursor() as cur:
        cur.execute(
            "CREATE TABLE IF NOT EXISTS _migrations ("
            "  name TEXT PRIMARY KEY,"
            "  applied_at TEXT NOT NULL DEFAULT (datetime('now'))"
            ")"
        )
        applied = {row["name"] for row in cur.execute("SELECT name FROM _migrations")}
        for path in sorted(_MIGRATIONS_DIR.glob("*.sql")):
            if path.name in applied:
                continue
            cur.executescript(path.read_text())
            cur.execute("INSERT INTO _migrations(name) VALUES (?)", (path.name,))
