"""Trigger repo. The YAML file in the wiki is the source of truth; SQLite mirrors it.

Mutation order is always: write/delete file → upsert/delete row, in that order.
If the SQLite write fails after the file commit, ``rebuild_from_filesystem``
will re-converge the cache; the inverse (file fails after the row) leaves
nothing behind because the row write is the second step.

Format (every trigger has these three fields plus the standard scope/kind/enabled):
  * **if** — ``nl_description``: natural-language firing condition.
  * **message** — the notification body to deliver when the trigger fires.
  * **destination** — where to deliver. ``None`` (the only supported value in v0)
    means the Event Log: the fire is recorded as a ``trigger.fire`` event with
    the message in its payload, and nothing is dispatched outbound.

``message`` and ``destination`` are stored together in the ``action_json``
column (and the ``action`` block in the YAML file). The repo layer exposes
them as flat dict keys for callers.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from datetime import datetime, timezone

from app.db.sqlite import connect
from app.triggers import storage

log = logging.getLogger(__name__)

ALLOWED_KINDS = {"delta"}  # schedule support comes later
SUPPORTED_DESTINATIONS = {None}  # v0: Event Log only


def _action_payload(*, message: str, destination: object) -> str:
    return json.dumps({"message": message, "destination": destination})


def _parse_action(raw: str | None) -> dict:
    """Safely parse the ``action_json`` column. Returns ``{}`` on legacy rows."""
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        log.warning("trigger action_json invalid: %r", raw)
        return {}
    return data if isinstance(data, dict) else {}


def _row_to_dict(row: sqlite3.Row) -> dict:
    action = _parse_action(row["action_json"])
    return {
        "id": row["id"],
        "owner_user_id": row["owner_user_id"],
        "scope_path": row["scope_path"],
        "kind": row["kind"],
        "nl_description": row["nl_description"],
        "message": action.get("message"),
        "destination": action.get("destination"),
        "enabled": bool(row["enabled"]),
        "created_at": row["created_at"],
        "last_edited_at": row["last_edited_at"],
        "file_path": row["file_path"],
    }


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def create(
    *,
    owner_user_id: str,
    scope_path: str,
    nl_description: str,
    message: str,
    destination: object = None,
    kind: str = "delta",
    enabled: bool = True,
    actor: str | None = None,
) -> dict:
    if kind not in ALLOWED_KINDS:
        raise ValueError(f"unsupported kind: {kind!r}")
    if not isinstance(nl_description, str) or not nl_description.strip():
        raise ValueError(
            "nl_description (the firing condition) is required and must be a "
            "non-empty string"
        )
    if not isinstance(message, str) or not message.strip():
        raise ValueError(
            "message (the fire message) is required and must be a non-empty string"
        )
    if destination not in SUPPORTED_DESTINATIONS:
        raise ValueError(
            f"destination {destination!r} not supported in v0 — only null (Event Log)"
        )

    trigger_id = "trg_" + uuid.uuid4().hex[:12]
    created_at = _now_iso()
    file_path = storage.compute_path(scope_path=scope_path, trigger_id=trigger_id)
    trigger = {
        "id": trigger_id,
        "owner_user_id": owner_user_id,
        "scope_path": scope_path,
        "kind": kind,
        "nl_description": nl_description,
        "message": message.strip(),
        "destination": destination,
        "enabled": enabled,
        "created_at": created_at,
    }
    storage.write_trigger(trigger, file_path=file_path, actor=actor)

    conn = connect()
    try:
        conn.execute(
            "INSERT INTO triggers(id, owner_user_id, scope_path, kind, nl_description, "
            "action_json, enabled, file_path, created_at, last_edited_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                trigger_id,
                owner_user_id,
                scope_path,
                kind,
                nl_description,
                _action_payload(message=message.strip(), destination=destination),
                1 if enabled else 0,
                file_path,
                created_at,
                created_at,
            ),
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


# Sentinel that means "leave this field alone" on update. ``None`` is a
# legitimate value for ``destination`` so we can't use it as the no-op marker.
_UNSET = object()


def update(
    trigger_id: str,
    *,
    scope_path: str | None = None,
    nl_description: str | None = None,
    message: str | None = None,
    destination: object = _UNSET,
    enabled: bool | None = None,
    actor: str | None = None,
) -> dict | None:
    existing = get(trigger_id)
    if existing is None:
        return None

    new = dict(existing)
    if scope_path is not None:
        new["scope_path"] = scope_path
    if nl_description is not None:
        if not isinstance(nl_description, str) or not nl_description.strip():
            raise ValueError("nl_description must be a non-empty string")
        new["nl_description"] = nl_description.strip()
    if message is not None:
        if not isinstance(message, str) or not message.strip():
            raise ValueError("message must be a non-empty string")
        new["message"] = message.strip()
    if destination is not _UNSET:
        if destination not in SUPPORTED_DESTINATIONS:
            raise ValueError(
                f"destination {destination!r} not supported in v0 — only null (Event Log)"
            )
        new["destination"] = destination
    if enabled is not None:
        new["enabled"] = enabled

    # Invariant: a saved trigger must always have both a firing condition
    # and a fire message. Catches the case where an existing legacy row had
    # one of them blank and the update doesn't touch the offending field.
    if not (isinstance(new.get("nl_description"), str) and new["nl_description"].strip()):
        raise ValueError(
            "nl_description (the firing condition) is required and must be a "
            "non-empty string"
        )
    if not (isinstance(new.get("message"), str) and new["message"].strip()):
        raise ValueError(
            "message (the fire message) is required and must be a non-empty string"
        )

    if (
        new["scope_path"] == existing["scope_path"]
        and new["nl_description"] == existing["nl_description"]
        and new["message"] == existing["message"]
        and new["destination"] == existing["destination"]
        and new["enabled"] == existing["enabled"]
    ):
        return existing

    new_file_path = storage.compute_path(
        scope_path=new["scope_path"], trigger_id=trigger_id
    )
    old_file_path = existing.get("file_path")
    if old_file_path and new_file_path != old_file_path:
        storage.move_trigger(
            new,
            old_file_path=old_file_path,
            new_file_path=new_file_path,
            actor=actor,
        )
    else:
        storage.write_trigger(new, file_path=new_file_path, actor=actor)

    conn = connect()
    try:
        conn.execute(
            "UPDATE triggers SET scope_path = ?, nl_description = ?, action_json = ?, "
            "enabled = ?, file_path = ?, last_edited_at = ? WHERE id = ?",
            (
                new["scope_path"],
                new["nl_description"],
                _action_payload(
                    message=new["message"] or "", destination=new["destination"]
                ),
                1 if new["enabled"] else 0,
                new_file_path,
                _now_iso(),
                trigger_id,
            ),
        )
        row = conn.execute("SELECT * FROM triggers WHERE id = ?", (trigger_id,)).fetchone()
    finally:
        conn.close()
    return _row_to_dict(row) if row else None


def delete(trigger_id: str, *, actor: str | None = None) -> bool:
    existing = get(trigger_id)
    if existing is None:
        return False
    if existing.get("file_path"):
        storage.delete_trigger(existing["file_path"], trigger_id, actor=actor)
    conn = connect()
    try:
        cur = conn.execute("DELETE FROM triggers WHERE id = ?", (trigger_id,))
        return cur.rowcount > 0
    finally:
        conn.close()


def _is_nonempty_string(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def purge_invalid_triggers(*, actor: str | None = None) -> int:
    """Delete trigger YAML files that violate the both-fields-required invariant.

    A saved trigger must carry both a non-empty firing condition
    (``nl_description``) and a non-empty fire message (``message``). Files
    that pre-date this rule (or otherwise drift) are removed from the wiki
    repo here so the cache rebuild that follows starts from a clean slate.
    Returns the number of files deleted.
    """
    deleted = 0
    for file_path in storage.list_all_files():
        try:
            data = storage.read_trigger(file_path)
        except Exception:
            log.warning(
                "purge_invalid_triggers: skip unreadable %s", file_path, exc_info=True
            )
            continue
        if _is_nonempty_string(data.get("nl_description")) and _is_nonempty_string(
            data.get("message")
        ):
            continue
        trigger_id = data.get("id") or "?"
        try:
            storage.delete_trigger(file_path, trigger_id, actor=actor)
            deleted += 1
            log.info(
                "purge_invalid_triggers: removed %s id=%s (missing required field)",
                file_path,
                trigger_id,
            )
        except Exception:
            log.exception(
                "purge_invalid_triggers: failed to delete %s id=%s",
                file_path,
                trigger_id,
            )
    return deleted


def rebuild_from_filesystem() -> int:
    """Repopulate the SQLite cache from on-disk trigger files.

    Files are authoritative — the cache is wiped and rebuilt from whatever
    YAML the wiki currently tracks. Returns the number of triggers loaded.
    """
    parsed: list[tuple[str, dict]] = []
    skipped = 0
    for file_path in storage.list_all_files():
        try:
            data = storage.read_trigger(file_path)
        except Exception:
            log.warning("rebuild_from_filesystem: skip unreadable %s", file_path, exc_info=True)
            skipped += 1
            continue
        if not (
            _is_nonempty_string(data.get("nl_description"))
            and _is_nonempty_string(data.get("message"))
        ):
            log.warning(
                "rebuild_from_filesystem: skip %s (missing required field)",
                file_path,
            )
            skipped += 1
            continue
        parsed.append((file_path, data))

    conn = connect()
    try:
        conn.execute("DELETE FROM triggers")
        for file_path, data in parsed:
            action_payload = _action_payload(
                message=data.get("message") or "",
                destination=data.get("destination"),
            )
            conn.execute(
                "INSERT INTO triggers(id, owner_user_id, scope_path, kind, "
                "nl_description, action_json, enabled, file_path, created_at, "
                "last_edited_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, COALESCE(?, datetime('now')), "
                "COALESCE(?, ?, datetime('now')))",
                (
                    data["id"],
                    data["owner_user_id"],
                    data["scope_path"],
                    data["kind"],
                    data["nl_description"],
                    action_payload,
                    1 if data.get("enabled", True) else 0,
                    file_path,
                    data.get("created_at"),
                    data.get("last_edited_at"),
                    data.get("created_at"),
                ),
            )
    finally:
        conn.close()
    log.info("rebuild_from_filesystem loaded=%d skipped=%d", len(parsed), skipped)
    return len(parsed)
