"""User repo — direct SQLite. Small enough that an ORM would be overkill."""
from __future__ import annotations

import logging
import sqlite3
import uuid

from app.auth.passwords import hash_password
from app.db.sqlite import connect

log = logging.getLogger(__name__)


def get_by_email(email: str) -> sqlite3.Row | None:
    conn = connect()
    try:
        return conn.execute(
            "SELECT id, email, name, password_hash, is_admin FROM users WHERE email = ?",
            (email.lower(),),
        ).fetchone()
    finally:
        conn.close()


def get_by_id(user_id: str) -> sqlite3.Row | None:
    conn = connect()
    try:
        return conn.execute(
            "SELECT id, email, name, password_hash, is_admin FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
    finally:
        conn.close()


def count() -> int:
    conn = connect()
    try:
        return conn.execute("SELECT count(*) AS n FROM users").fetchone()["n"]
    finally:
        conn.close()


def list_all() -> list[sqlite3.Row]:
    conn = connect()
    try:
        return conn.execute(
            "SELECT id, email, name, is_admin, created_at FROM users ORDER BY created_at ASC"
        ).fetchall()
    finally:
        conn.close()


def create(email: str, password: str, name: str | None = None) -> str:
    """Create a user. The very first user is auto-promoted to admin."""
    user_id = str(uuid.uuid4())
    conn = connect()
    try:
        is_admin = 1 if conn.execute("SELECT count(*) AS n FROM users").fetchone()["n"] == 0 else 0
        conn.execute(
            "INSERT INTO users (id, email, name, password_hash, is_admin) VALUES (?, ?, ?, ?, ?)",
            (user_id, email.lower(), name, hash_password(password), is_admin),
        )
    finally:
        conn.close()
    log.info("user created id=%s email=%s is_admin=%s", user_id, email.lower(), bool(is_admin))
    return user_id


def set_admin(user_id: str, is_admin: bool) -> None:
    conn = connect()
    try:
        conn.execute("UPDATE users SET is_admin = ? WHERE id = ?", (1 if is_admin else 0, user_id))
    finally:
        conn.close()


def admin_count() -> int:
    conn = connect()
    try:
        return conn.execute("SELECT count(*) AS n FROM users WHERE is_admin = 1").fetchone()["n"]
    finally:
        conn.close()


def delete(user_id: str) -> None:
    conn = connect()
    try:
        conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
    finally:
        conn.close()
