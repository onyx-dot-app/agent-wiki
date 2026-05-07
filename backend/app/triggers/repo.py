"""Trigger repo — direct SQLite, mirrors ``app/auth/users.py``.

V0 keeps everything in SQLite (no YAML, no git history for triggers).
``triggers.action_json`` is required by the schema but unused in v0; we
always store ``'{}'``.
"""
from __future__ import annotations

import sqlite3
import uuid

from app.db.sqlite import connect

ALLOWED_KINDS = {"delta"}  # schedule support comes later


def _row_to_dict(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "owner_user_id": row["owner_user_id"],
        "scope_path": row["scope_path"],
        "kind": row["kind"],
        "nl_description": row["nl_description"],
        "enabled": bool(row["enabled"]),
        "created_at": row["created_at"],
    }


def create(
    *,
    owner_user_id: str,
    scope_path: str,
    nl_description: str,
    kind: str = "delta",
    enabled: bool = True,
) -> dict:
    if kind not in ALLOWED_KINDS:
        raise ValueError(f"unsupported kind: {kind!r}")
    trigger_id = "trg_" + uuid.uuid4().hex[:12]
    conn = connect()
    try:
        conn.execute(
            "INSERT INTO triggers(id, owner_user_id, scope_path, kind, nl_description, action_json, enabled) "
            "VALUES (?, ?, ?, ?, ?, '{}', ?)",
            (trigger_id, owner_user_id, scope_path, kind, nl_description, 1 if enabled else 0),
        )
        row = conn.execute("SELECT * FROM triggers WHERE id = ?", (trigger_id,)).fetchone()
    finally:
        conn.close()
    return _row_to_dict(row)


def get(trigger_id: str) -> dict | None:
    conn = connect()
    try:
        row = conn.execute("SELECT * FROM triggers WHERE id = ?", (trigger_id,)).fetchone()
    finally:
        conn.close()
    return _row_to_dict(row) if row else None


def list_for_owner(owner_user_id: str) -> list[dict]:
    conn = connect()
    try:
        rows = conn.execute(
            "SELECT * FROM triggers WHERE owner_user_id = ? ORDER BY created_at DESC",
            (owner_user_id,),
        ).fetchall()
    finally:
        conn.close()
    return [_row_to_dict(r) for r in rows]


def update(
    trigger_id: str,
    *,
    scope_path: str | None = None,
    nl_description: str | None = None,
    enabled: bool | None = None,
) -> dict | None:
    sets: list[str] = []
    args: list[object] = []
    if scope_path is not None:
        sets.append("scope_path = ?")
        args.append(scope_path)
    if nl_description is not None:
        sets.append("nl_description = ?")
        args.append(nl_description)
    if enabled is not None:
        sets.append("enabled = ?")
        args.append(1 if enabled else 0)
    if not sets:
        return get(trigger_id)
    args.append(trigger_id)
    conn = connect()
    try:
        conn.execute(f"UPDATE triggers SET {', '.join(sets)} WHERE id = ?", args)
        row = conn.execute("SELECT * FROM triggers WHERE id = ?", (trigger_id,)).fetchone()
    finally:
        conn.close()
    return _row_to_dict(row) if row else None


def delete(trigger_id: str) -> bool:
    conn = connect()
    try:
        cur = conn.execute("DELETE FROM triggers WHERE id = ?", (trigger_id,))
        return cur.rowcount > 0
    finally:
        conn.close()
