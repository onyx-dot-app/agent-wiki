"""Trigger repo. The YAML file in the wiki is the source of truth; the
``triggers`` Postgres table is a cache.

Mutation order is always: write/delete file → upsert/delete row, in that order.
If the DB write fails after the file commit, ``rebuild_from_filesystem``
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
import uuid
from datetime import datetime, timezone
from typing import Any, cast

from sqlalchemy import delete as sa_delete, select

from app.db.models import Trigger
from app.db.session import session
from app.triggers import storage

log = logging.getLogger(__name__)

ALLOWED_KINDS = {"delta"}              # schedule support comes later
SUPPORTED_DESTINATIONS = {None}        # v0: Event Log only


def _action_payload(*, message: str, destination: object) -> str:
    return json.dumps({"message": message, "destination": destination})


def _parse_action(raw: str | None) -> dict[str, Any]:
    """Safely parse the ``action_json`` column. Returns ``{}`` on legacy rows."""
    if not raw:
        return {}
    try:
        data: object = json.loads(raw)
    except (TypeError, ValueError):
        log.warning("trigger action_json invalid: %r", raw)
        return {}
    if isinstance(data, dict):
        return cast(dict[str, Any], data)
    return {}


def _to_dict(t: Trigger) -> dict[str, Any]:
    action = _parse_action(t.action_json)
    return {
        "id": t.id,
        "owner_user_id": t.owner_user_id,
        "scope_path": t.scope_path,
        "kind": t.kind,
        "nl_description": t.nl_description,
        "message": action.get("message"),
        "destination": action.get("destination"),
        "enabled": t.enabled,
        "created_at": t.created_at,
        "last_edited_at": t.last_edited_at,
        "file_path": t.file_path,
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
) -> dict[str, Any]:
    if kind not in ALLOWED_KINDS:
        raise ValueError(f"unsupported kind: {kind!r}")
    if not nl_description.strip():
        raise ValueError(
            "nl_description (the firing condition) is required and must be a "
            "non-empty string"
        )
    if not message.strip():
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
    row_dict = {
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
    storage.write_trigger(row_dict, file_path=file_path, actor=actor)

    with session() as s:
        s.add(
            Trigger(
                id=trigger_id,
                owner_user_id=owner_user_id,
                scope_path=scope_path,
                kind=kind,
                nl_description=nl_description,
                action_json=_action_payload(
                    message=message.strip(), destination=destination
                ),
                enabled=enabled,
                file_path=file_path,
                created_at=created_at,
                last_edited_at=created_at,
            )
        )
        s.flush()
        t = s.get(Trigger, trigger_id)
        assert t is not None
        return _to_dict(t)


def get(trigger_id: str) -> dict[str, Any] | None:
    with session() as s:
        t = s.get(Trigger, trigger_id)
        return _to_dict(t) if t else None


def list_for_owner(owner_user_id: str) -> list[dict[str, Any]]:
    with session() as s:
        rows = s.scalars(
            select(Trigger)
            .where(Trigger.owner_user_id == owner_user_id)
            .order_by(Trigger.created_at.desc())
        ).all()
        return [_to_dict(t) for t in rows]


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
) -> dict[str, Any] | None:
    existing = get(trigger_id)
    if existing is None:
        return None

    new = dict(existing)
    if scope_path is not None:
        new["scope_path"] = scope_path
    if nl_description is not None:
        if not nl_description.strip():
            raise ValueError("nl_description must be a non-empty string")
        new["nl_description"] = nl_description.strip()
    if message is not None:
        if not message.strip():
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
    # and a fire message.
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

    with session() as s:
        t = s.get(Trigger, trigger_id)
        if t is None:
            return None
        t.scope_path = new["scope_path"]
        t.nl_description = new["nl_description"]
        t.action_json = _action_payload(
            message=new["message"] or "", destination=new["destination"]
        )
        t.enabled = new["enabled"]
        t.file_path = new_file_path
        t.last_edited_at = _now_iso()
        s.flush()
        return _to_dict(t)


def delete(trigger_id: str, *, actor: str | None = None) -> bool:
    existing = get(trigger_id)
    if existing is None:
        return False
    if existing.get("file_path"):
        storage.delete_trigger(existing["file_path"], trigger_id, actor=actor)
    with session() as s:
        t = s.get(Trigger, trigger_id)
        if t is None:
            return False
        s.delete(t)
        return True


def _is_nonempty_string(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def purge_invalid_triggers(*, actor: str | None = None) -> int:
    """Delete trigger YAML files that violate the both-fields-required invariant."""
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
    """Repopulate the cache from on-disk trigger files. Returns count loaded."""
    from app.db.models import User

    parsed: list[tuple[str, dict[str, Any]]] = []
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

    fallback_now = _now_iso()
    with session() as s:
        s.execute(sa_delete(Trigger))

        # Triggers reference ``users.id``; the wiki may carry YAMLs from an
        # owner that no longer exists in the DB (e.g. a fresh
        # ``Base.metadata.create_all`` against this Postgres instance).
        # Skip orphans rather than failing the whole rebuild.
        known_user_ids = {row for row in s.scalars(select(User.id)).all()}

        loaded = 0
        for file_path, data in parsed:
            owner_id = data["owner_user_id"]
            if owner_id not in known_user_ids:
                if data.get("enabled", True):
                    disabled = dict(data)
                    disabled["enabled"] = False
                    try:
                        storage.write_trigger(
                            disabled, file_path=file_path, actor=None
                        )
                        log.warning(
                            "rebuild_from_filesystem: disabled %s (owner_user_id=%s not in users)",
                            file_path, owner_id,
                        )
                    except Exception:
                        log.exception(
                            "rebuild_from_filesystem: failed to disable %s (owner_user_id=%s)",
                            file_path, owner_id,
                        )
                else:
                    log.warning(
                        "rebuild_from_filesystem: skip %s (owner_user_id=%s not in users; already disabled)",
                        file_path, owner_id,
                    )
                skipped += 1
                continue

            action_payload = _action_payload(
                message=data.get("message") or "",
                destination=data.get("destination"),
            )
            created_at = data.get("created_at") or fallback_now
            last_edited = data.get("last_edited_at") or created_at
            s.add(
                Trigger(
                    id=data["id"],
                    owner_user_id=owner_id,
                    scope_path=data["scope_path"],
                    kind=data["kind"],
                    nl_description=data["nl_description"],
                    action_json=action_payload,
                    enabled=bool(data.get("enabled", True)),
                    file_path=file_path,
                    created_at=created_at,
                    last_edited_at=last_edited,
                )
            )
            loaded += 1
    log.info("rebuild_from_filesystem loaded=%d skipped=%d", loaded, skipped)
    return loaded
