"""Trigger repo. The YAML file in the wiki is the source of truth; the
``triggers`` Postgres table is a cache.

Mutation order is always: write/delete file → upsert/delete row, in that order.
If the DB write fails after the file commit, ``rebuild_from_filesystem``
will re-converge the cache; the inverse (file fails after the row) leaves
nothing behind because the row write is the second step.

Format (every trigger has these three fields plus the standard scope/kind/enabled):
  * **if** — ``nl_description``: natural-language firing condition.
  * **message** — the notification body to deliver when the trigger fires.
  * **destination** — slug of a row in ``trigger_destinations``. Defaults to
    ``"event_log"`` (record the fire to the events table; no outbound
    dispatch). New destinations are added by migration as their dispatchers
    come online — see ``app/triggers/destinations.py``.

``message`` and ``destination`` are stored together in the ``action_json``
column (and the ``action`` block in the YAML file). The repo layer exposes
them as flat dict keys for callers. Legacy rows where ``destination`` is
``None`` (predating the destinations catalog) are read as ``"event_log"``
so callers don't need to special-case the migration boundary.
"""

from __future__ import annotations

import json
import logging
import posixpath
import uuid
from datetime import datetime, timezone
from typing import Any, cast
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from croniter import croniter
from sqlalchemy import delete as sa_delete, or_, select

from app.db.models import Event, Trigger
from app.db.session import session
from app.slack import webhooks as slack_webhooks
from app.triggers import destinations as destinations_repo
from app.triggers import storage

log = logging.getLogger(__name__)

ALLOWED_KINDS = {"delta", "schedule"}

# Default destination for new triggers — fires go to the events table.
DEFAULT_DESTINATION = destinations_repo.EVENT_LOG_ID


def _validate_destination(destination: object) -> str:
    """Coerce + validate a destination value into a known slug.

    Returns the canonical slug. Raises ``ValueError`` if the value isn't a
    string or doesn't match a row in ``trigger_destinations``.
    """
    if destination is None:
        return destinations_repo.EVENT_LOG_ID
    if not isinstance(destination, str) or not destination.strip():
        raise ValueError(f"destination must be a destination id string; got {destination!r}")
    slug = destination.strip()
    if not destinations_repo.exists(slug):
        raise ValueError(
            f"destination {slug!r} not found — call get_trigger_destinations to list available ids"
        )
    return slug


def _validate_slack_webhook(
    *, destination: str, slack_webhook_id: object, owner_user_id: str
) -> str | None:
    """Resolve the Slack channel reference for a trigger.

    A ``slack`` destination requires a ``slack_webhook_id`` that the owner
    actually owns. Any other destination must not carry one (we null it so a
    destination flip doesn't leave a dangling reference).
    """
    if destination != destinations_repo.SLACK_ID:
        return None
    if not isinstance(slack_webhook_id, str) or not slack_webhook_id.strip():
        raise ValueError("a Slack destination requires slack_webhook_id (the channel to post to)")
    wid = slack_webhook_id.strip()
    if not slack_webhooks.owned_by(wid, owner_user_id):
        raise ValueError(f"slack_webhook_id {wid!r} not found")
    return wid


def _action_payload(*, message: str, destination: object) -> str:
    return json.dumps({"message": message, "destination": destination})


def _validate_schedule_fields(
    *,
    kind: str,
    schedule_cron: str | None,
    schedule_timezone: str | None,
    schedule_start_at: str | None,
) -> tuple[str | None, str | None, str | None]:
    """Validate (and normalize) the schedule fields.

    For ``kind="schedule"`` the cron and timezone are required and must
    parse. ``schedule_start_at`` is optional but must be ISO 8601 if
    present. For ``kind="delta"`` all three must be ``None`` (a delta
    trigger with a cron is just confusing — refuse rather than silently
    ignore). Returns the canonicalized triple.
    """
    if kind == "schedule":
        if not schedule_cron or not schedule_cron.strip():
            raise ValueError("schedule_cron is required for schedule triggers")
        cron = schedule_cron.strip()
        if not croniter.is_valid(cron):
            raise ValueError(f"schedule_cron {cron!r} is not a valid 5-field cron expression")

        if not schedule_timezone or not schedule_timezone.strip():
            raise ValueError("schedule_timezone is required for schedule triggers")
        tz = schedule_timezone.strip()
        try:
            ZoneInfo(tz)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"schedule_timezone {tz!r} is not a known IANA name") from exc

        start_at: str | None = None
        if schedule_start_at is not None:
            if not schedule_start_at.strip():
                raise ValueError("schedule_start_at must be an ISO 8601 string or null")
            try:
                # ``fromisoformat`` accepts naive too; we normalize to UTC for storage.
                parsed = datetime.fromisoformat(schedule_start_at.strip())
            except ValueError as exc:
                raise ValueError(
                    f"schedule_start_at {schedule_start_at!r} is not a valid ISO 8601 timestamp"
                ) from exc
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            start_at = parsed.astimezone(timezone.utc).isoformat(timespec="seconds")
        return cron, tz, start_at

    # kind == "delta": schedule fields must be unset.
    if any(v is not None for v in (schedule_cron, schedule_timezone, schedule_start_at)):
        raise ValueError("schedule_* fields must be null for delta triggers")
    return None, None, None


def _parse_action(raw: str) -> dict[str, Any]:
    """Parse the ``action_json`` column."""
    return cast(dict[str, Any], json.loads(raw))


def _to_dict(t: Trigger) -> dict[str, Any]:
    action = _parse_action(t.action_json)
    return {
        "id": t.id,
        "owner_user_id": t.owner_user_id,
        "scope_path": t.scope_path,
        "kind": t.kind,
        "nl_description": t.nl_description,
        "message": action.get("message"),
        "destination": action["destination"],
        "slack_webhook_id": t.slack_webhook_id,
        "enabled": t.enabled,
        "created_at": t.created_at,
        "last_edited_at": t.last_edited_at,
        "file_path": t.file_path,
        "schedule_cron": t.schedule_cron,
        "schedule_timezone": t.schedule_timezone,
        "schedule_start_at": t.schedule_start_at,
        "schedule_last_fired_at": t.schedule_last_fired_at,
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
    slack_webhook_id: object = None,
    kind: str = "delta",
    enabled: bool = True,
    actor: str | None = None,
    schedule_cron: str | None = None,
    schedule_timezone: str | None = None,
    schedule_start_at: str | None = None,
) -> dict[str, Any]:
    if kind not in ALLOWED_KINDS:
        raise ValueError(f"unsupported kind: {kind!r}")
    if not nl_description.strip():
        raise ValueError(
            "nl_description (the firing condition) is required and must be a non-empty string"
        )
    if not message.strip():
        raise ValueError("message (the fire message) is required and must be a non-empty string")
    destination_id = _validate_destination(destination)
    webhook_id = _validate_slack_webhook(
        destination=destination_id,
        slack_webhook_id=slack_webhook_id,
        owner_user_id=owner_user_id,
    )
    cron_value, tz_value, start_at_value = _validate_schedule_fields(
        kind=kind,
        schedule_cron=schedule_cron,
        schedule_timezone=schedule_timezone,
        schedule_start_at=schedule_start_at,
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
        "destination": destination_id,
        "slack_webhook_id": webhook_id,
        "enabled": enabled,
        "created_at": created_at,
        "schedule_cron": cron_value,
        "schedule_timezone": tz_value,
        "schedule_start_at": start_at_value,
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
                action_json=_action_payload(message=message.strip(), destination=destination_id),
                slack_webhook_id=webhook_id,
                enabled=enabled,
                file_path=file_path,
                created_at=created_at,
                last_edited_at=created_at,
                schedule_cron=cron_value,
                schedule_timezone=tz_value,
                schedule_start_at=start_at_value,
                schedule_last_fired_at=None,
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
    slack_webhook_id: object = _UNSET,
    enabled: bool | None = None,
    actor: str | None = None,
    schedule_cron: str | None = None,
    schedule_timezone: str | None = None,
    schedule_start_at: object = _UNSET,
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
        new["destination"] = _validate_destination(destination)
    if slack_webhook_id is not _UNSET:
        new["slack_webhook_id"] = slack_webhook_id
    # Re-resolve the channel reference against the *final* destination: a flip
    # to slack requires a webhook, a flip away from slack nulls it.
    new["slack_webhook_id"] = _validate_slack_webhook(
        destination=new["destination"],
        slack_webhook_id=new.get("slack_webhook_id"),
        owner_user_id=existing["owner_user_id"],
    )
    if enabled is not None:
        new["enabled"] = enabled
    if schedule_cron is not None:
        new["schedule_cron"] = schedule_cron
    if schedule_timezone is not None:
        new["schedule_timezone"] = schedule_timezone
    # ``None`` is a legitimate value for ``schedule_start_at`` (clear the
    # anchor), so we use the _UNSET sentinel as the no-op marker.
    if schedule_start_at is not _UNSET:
        new["schedule_start_at"] = cast(str | None, schedule_start_at)

    # Invariant: a saved trigger must always have both a firing condition
    # and a fire message.
    if not (isinstance(new.get("nl_description"), str) and new["nl_description"].strip()):
        raise ValueError(
            "nl_description (the firing condition) is required and must be a non-empty string"
        )
    if not (isinstance(new.get("message"), str) and new["message"].strip()):
        raise ValueError("message (the fire message) is required and must be a non-empty string")

    cron_value, tz_value, start_at_value = _validate_schedule_fields(
        kind=new.get("kind", "delta"),
        schedule_cron=new.get("schedule_cron"),
        schedule_timezone=new.get("schedule_timezone"),
        schedule_start_at=new.get("schedule_start_at"),
    )
    new["schedule_cron"] = cron_value
    new["schedule_timezone"] = tz_value
    new["schedule_start_at"] = start_at_value

    if (
        new["scope_path"] == existing["scope_path"]
        and new["nl_description"] == existing["nl_description"]
        and new["message"] == existing["message"]
        and new["destination"] == existing["destination"]
        and new.get("slack_webhook_id") == existing.get("slack_webhook_id")
        and new["enabled"] == existing["enabled"]
        and new.get("schedule_cron") == existing.get("schedule_cron")
        and new.get("schedule_timezone") == existing.get("schedule_timezone")
        and new.get("schedule_start_at") == existing.get("schedule_start_at")
    ):
        return existing

    new_file_path = storage.compute_path(scope_path=new["scope_path"], trigger_id=trigger_id)
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
        t.slack_webhook_id = new.get("slack_webhook_id")
        t.enabled = new["enabled"]
        t.file_path = new_file_path
        t.last_edited_at = _now_iso()
        t.schedule_cron = cron_value
        t.schedule_timezone = tz_value
        t.schedule_start_at = start_at_value
        s.flush()
        return _to_dict(t)


def record_schedule_fire(trigger_id: str, fired_at: str) -> None:
    """Stamp ``schedule_last_fired_at`` on the trigger row.

    The schedule evaluator calls this on every tick that processed the
    trigger — match or no-match — so croniter advances and the same tick
    isn't re-evaluated next pass. Quiet no-op if the trigger is gone.
    """
    with session() as s:
        t = s.get(Trigger, trigger_id)
        if t is None:
            return
        t.schedule_last_fired_at = fired_at


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
            log.warning("purge_invalid_triggers: skip unreadable %s", file_path, exc_info=True)
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


def _is_trigger_file(path: str) -> bool:
    name = posixpath.basename(path)
    return name.startswith(".trigger_") and name.endswith(".yaml")


def repoint_scopes_for_moves(moves: list[tuple[str, str]], *, actor: str | None) -> None:
    """Rewrite trigger scopes after a path move so they don't dangle.

    A trigger's ``scope_path`` lives *inside* its committed YAML, and the
    YAML's filename/location is derived from that scope (``compute_path``). A
    plain ``git mv`` relocates files but never rewrites YAML content, so a
    rename leaves trigger scopes pointing at paths that no longer exist. Fix
    the YAML files here; the caller (``after_path_move``) reconverges the
    Postgres cache via ``rebuild_from_filesystem`` immediately after, so this
    only touches the on-disk source of truth.

    Two cases, read off the per-file ``(old, new)`` pairs:
      A. A trigger YAML rode along in a folder move (it's a tracked file under
         the moved folder). It's already at ``new`` on disk but still names the
         old scope — invert ``compute_path`` against its new location to get
         the corrected scope and rewrite the content in place.
      B. A ``.md`` doc moved but its sibling doc-scoped trigger YAML did *not*
         (a single-file rename doesn't sweep sibling files). Relocate + rewrite
         the YAML to match the doc's new path.
    """
    handled_ids: set[str] = set()

    # Case A — trigger YAMLs that physically moved with a folder.
    for old_p, new_p in moves:
        if old_p == new_p or not _is_trigger_file(old_p):
            continue
        try:
            data = storage.read_trigger(new_p)  # at new_p post-mv; old scope still inside
        except Exception:
            log.warning("repoint_scopes_for_moves: unreadable trigger %s", new_p, exc_info=True)
            continue
        old_scope = data.get("scope_path") or ""
        new_dir = posixpath.dirname(new_p)
        if storage.kind_of_scope(old_scope) == "doc":
            base = posixpath.basename(old_scope)
            new_scope = f"{new_dir}/{base}" if new_dir else base
        else:
            new_scope = new_dir
        handled_ids.add(data["id"])
        if new_scope == old_scope:
            continue
        data["scope_path"] = new_scope
        storage.write_trigger(data, file_path=new_p, actor=actor)

    # Case B — docs whose sibling doc-scoped trigger YAML stayed put.
    moved_yaml_olds = {old_p for old_p, _ in moves if _is_trigger_file(old_p)}
    for old_p, new_p in moves:
        if old_p == new_p or not old_p.endswith(".md"):
            continue
        with session() as s:
            triggers = [
                _to_dict(t)
                for t in s.scalars(select(Trigger).where(Trigger.scope_path == old_p)).all()
            ]
        for trig in triggers:
            old_file_path = trig.get("file_path")
            if (
                trig["id"] in handled_ids
                or not old_file_path
                or old_file_path in moved_yaml_olds
            ):
                continue
            handled_ids.add(trig["id"])
            new_file_path = storage.compute_path(scope_path=new_p, trigger_id=trig["id"])
            trig["scope_path"] = new_p
            if new_file_path == old_file_path:
                storage.write_trigger(trig, file_path=new_file_path, actor=actor)
            else:
                storage.move_trigger(
                    trig, old_file_path=old_file_path, new_file_path=new_file_path, actor=actor
                )


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
        # schedule_last_fired_at is runtime cursor state, intentionally not
        # stored in the YAML — so carry it across the rebuild instead of
        # nulling it, or every rebuild (boot, and per-move via after_path_move)
        # would reset the schedule cursor and re-evaluate fired windows.
        preserved_last_fired: dict[str, str | None] = {
            tid: last
            for tid, last in s.execute(
                select(Trigger.id, Trigger.schedule_last_fired_at).where(
                    Trigger.schedule_last_fired_at.is_not(None)
                )
            ).all()
        }
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
                        storage.write_trigger(disabled, file_path=file_path, actor=None)
                        log.warning(
                            "rebuild_from_filesystem: disabled %s (owner_user_id=%s not in users)",
                            file_path,
                            owner_id,
                        )
                    except Exception:
                        log.exception(
                            "rebuild_from_filesystem: failed to disable %s (owner_user_id=%s)",
                            file_path,
                            owner_id,
                        )
                else:
                    log.warning(
                        "rebuild_from_filesystem: skip %s (owner_user_id=%s not in users; already disabled)",
                        file_path,
                        owner_id,
                    )
                skipped += 1
                continue

            action_payload = _action_payload(
                message=data.get("message") or "",
                destination=_validate_destination(data.get("destination")),
            )
            created_at = data.get("created_at") or fallback_now
            last_edited = data.get("last_edited_at") or created_at
            try:
                cron_value, tz_value, start_at_value = _validate_schedule_fields(
                    kind=data["kind"],
                    schedule_cron=data.get("schedule_cron"),
                    schedule_timezone=data.get("schedule_timezone"),
                    schedule_start_at=data.get("schedule_start_at"),
                )
            except ValueError:
                log.warning(
                    "rebuild_from_filesystem: skip %s (invalid schedule fields)",
                    file_path,
                    exc_info=True,
                )
                skipped += 1
                continue
            s.add(
                Trigger(
                    id=data["id"],
                    owner_user_id=owner_id,
                    scope_path=data["scope_path"],
                    kind=data["kind"],
                    nl_description=data["nl_description"],
                    action_json=action_payload,
                    # The Slack channel link the UI resolves to a channel name.
                    slack_webhook_id=data.get("slack_webhook_id"),
                    enabled=bool(data.get("enabled", True)),
                    file_path=file_path,
                    created_at=created_at,
                    last_edited_at=last_edited,
                    schedule_cron=cron_value,
                    schedule_timezone=tz_value,
                    schedule_start_at=start_at_value,
                    schedule_last_fired_at=preserved_last_fired.get(data["id"]),
                )
            )
            loaded += 1
    log.info("rebuild_from_filesystem loaded=%d skipped=%d", loaded, skipped)
    return loaded


def fire_counts_by_sha(shas: set[str]) -> dict[str, int]:
    """``{sha: number_of_trigger_fires}`` for the given commit shas.

    Counts ``trigger.fire`` audit events whose payload records the source
    commit sha that fired them. ``payload_json`` is plain text (not JSONB),
    so we coarse-filter on the sha substring in SQL, then confirm the exact
    ``sha`` field in Python (the substring match is serialization-agnostic;
    the Python check is what guarantees correctness).
    """
    if not shas:
        return {}
    counts: dict[str, int] = {}
    with session() as s:
        stmt = select(Event.payload_json).where(
            Event.kind == "trigger.fire",
            Event.payload_json.isnot(None),
            or_(*(Event.payload_json.contains(sha) for sha in shas)),
        )
        payloads = s.execute(stmt).scalars().all()
    for payload_json in payloads:
        try:
            parsed: Any = json.loads(payload_json) if payload_json else {}
        except json.JSONDecodeError:
            continue
        if not isinstance(parsed, dict):
            continue
        payload = cast("dict[str, Any]", parsed)
        sha = payload.get("sha")
        if isinstance(sha, str) and sha in shas:
            counts[sha] = counts.get(sha, 0) + 1
    return counts
