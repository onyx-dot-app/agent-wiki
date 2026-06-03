"""Trigger evaluation.

Two flavors:
  * **delta**     — fires when a doc (or any doc under a directory scope) gets
                    a meaningful update. Uses an LLM check against the
                    ``nl_description`` of the trigger.
  * **schedule**  — fires on cron, evaluated by the periodic task. Same NL
                    gate as delta, but the payload is a snapshot of the
                    current wiki (no diff) since there's no commit.

When a doc is updated we evaluate triggers whose ``scope_path`` matches the
doc itself or any parent directory. When the schedule scheduler ticks we
load all enabled schedule triggers and ask croniter whether each is due.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from croniter import croniter
from pydantic import BaseModel
from sqlalchemy import select

from app.db.models import Trigger
from app.db.session import session
from app.triggers.natural_language import (
    MatchResult,
    NewFileEvalResult,
    evaluate_new_file_in_dir as nl_evaluate_new_file_in_dir,
)
from app.triggers.natural_language import matches as nl_matches
from app.triggers.natural_language import matches_snapshot as nl_matches_snapshot
from app.triggers.natural_language import render_message as nl_render_message
from app.triggers.natural_language import (
    render_snapshot_message as nl_render_snapshot_message,
)
from app.triggers.repo import _parse_action  # pyright: ignore[reportPrivateUsage]
from app.wiki.filesystem import parent_dirs

log = logging.getLogger(__name__)


class TriggerRecord(BaseModel):
    """Eval-side view of a trigger row. ``message`` and ``destination`` are
    parsed from ``Trigger.action_json``; raw column shape is hidden."""

    id: str
    owner_user_id: str
    scope_path: str
    kind: str
    nl_description: str
    message: str | None
    destination: str
    slack_webhook_id: str | None = None
    enabled: bool
    file_path: str | None
    created_at: str | None
    last_edited_at: str | None
    schedule_cron: str | None = None
    schedule_timezone: str | None = None
    schedule_start_at: str | None = None
    schedule_last_fired_at: str | None = None


def _to_record(t: Trigger) -> TriggerRecord:
    action = _parse_action(t.action_json)
    return TriggerRecord(
        id=t.id,
        owner_user_id=t.owner_user_id,
        scope_path=t.scope_path,
        kind=t.kind,
        nl_description=t.nl_description,
        message=action.get("message"),
        destination=action["destination"],
        slack_webhook_id=t.slack_webhook_id,
        enabled=t.enabled,
        file_path=t.file_path,
        created_at=t.created_at,
        last_edited_at=t.last_edited_at,
        schedule_cron=t.schedule_cron,
        schedule_timezone=t.schedule_timezone,
        schedule_start_at=t.schedule_start_at,
        schedule_last_fired_at=t.schedule_last_fired_at,
    )


def find_matching_triggers(doc_path: str) -> list[TriggerRecord]:
    """Return enabled delta triggers attached to ``doc_path`` or any parent dir."""
    candidates = [doc_path, *parent_dirs(doc_path)]
    with session() as s:
        rows = s.scalars(
            select(Trigger).where(
                Trigger.kind == "delta",
                Trigger.enabled.is_(True),
                Trigger.scope_path.in_(candidates),
            )
        ).all()
        return [_to_record(t) for t in rows]


def find_due_schedule_triggers(now: datetime) -> list[TriggerRecord]:
    """Return enabled schedule triggers whose cron has fired since their
    last evaluation.

    For each trigger we compute the next fire time after ``base`` (the
    most recent of ``schedule_last_fired_at``, ``schedule_start_at``, and
    ``created_at``) using croniter in the trigger's timezone, then
    compare to ``now`` in UTC. If the fire is due (≤ now), include it.

    A trigger that hasn't fired yet uses ``schedule_start_at`` (or
    ``created_at`` if no anchor) as the base, so a brand-new trigger
    fires on its first cron match — never on the moment of creation.

    Skips silently and logs on bad data (missing/invalid cron or tz);
    those rows would have been rejected by the repo at write time, but
    a corrupted YAML or hand-edited DB row shouldn't crash the loop.
    """
    now_utc = now.astimezone(timezone.utc) if now.tzinfo else now.replace(tzinfo=timezone.utc)
    out: list[TriggerRecord] = []
    with session() as s:
        rows = s.scalars(
            select(Trigger).where(
                Trigger.kind == "schedule",
                Trigger.enabled.is_(True),
            )
        ).all()
        for t in rows:
            try:
                if _is_due(t, now_utc):
                    out.append(_to_record(t))
            except Exception:
                log.exception(
                    "schedule eval: skip trigger %s (bad schedule fields)", t.id
                )
    return out


def _is_due(t: Trigger, now_utc: datetime) -> bool:
    """Decide whether a single schedule trigger's next cron fire is ≤ now."""
    if not t.schedule_cron or not t.schedule_timezone:
        return False
    tz = ZoneInfo(t.schedule_timezone)
    base = _schedule_base(t).astimezone(tz)
    next_fire_local = croniter(t.schedule_cron, base).get_next(datetime)
    next_fire_utc = next_fire_local.astimezone(timezone.utc)
    if t.schedule_start_at:
        anchor = datetime.fromisoformat(t.schedule_start_at)
        if anchor.tzinfo is None:
            anchor = anchor.replace(tzinfo=timezone.utc)
        if next_fire_utc < anchor:
            return False
    return next_fire_utc <= now_utc


def _schedule_base(t: Trigger) -> datetime:
    """Pick the croniter base: prefer ``last_fired_at`` and ``start_at``
    (max of whichever are set) over ``created_at``.

    Once a trigger has fired, ``last_fired_at`` is the only relevant
    floor — croniter advances from there. ``start_at`` keeps the trigger
    quiet until the anchor passes (and stays the floor afterwards in case
    it's bumped forward). ``created_at`` is the fallback for a brand-new
    trigger with no anchor and no fire history, so the first cron match
    after creation is the first fire (we don't backfire historical
    matches).
    """
    explicit: list[datetime] = []
    for raw in (t.schedule_last_fired_at, t.schedule_start_at):
        parsed = _parse_iso(raw)
        if parsed is not None:
            explicit.append(parsed)
    if explicit:
        return max(explicit)
    created = _parse_iso(t.created_at)
    if created is not None:
        return created
    return datetime.now(timezone.utc)


def _parse_iso(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def evaluate_delta(trigger: TriggerRecord, payload: str) -> MatchResult:
    """Phase 1 LLM check: does this delta satisfy ``trigger.nl_description``?

    ``payload`` is the combined wiki-snapshot + change view from
    ``app.triggers.diff.build_payload``. The reason is a one-line
    justification suitable for the events log.
    """
    return nl_matches(trigger.nl_description, payload)


def render_delta_message(
    message_instruction: str, payload: str, *, reason: str
) -> str:
    """Phase 2 LLM call: render the user-defined message for delivery."""
    return nl_render_message(message_instruction, payload, reason=reason)


def evaluate_schedule(trigger: TriggerRecord, payload: str) -> MatchResult:
    """Phase 1 LLM check for a schedule tick.

    ``payload`` is the wiki-snapshot + scheduled-check block from
    ``app.triggers.diff.build_schedule_payload``. The prompt asks the
    model to evaluate against current wiki state (no diff is available).
    """
    return nl_matches_snapshot(trigger.nl_description, payload)


def render_schedule_message(
    message_instruction: str, payload: str, *, reason: str
) -> str:
    """Phase 2 LLM call: render the schedule-fire notification text."""
    return nl_render_snapshot_message(message_instruction, payload, reason=reason)


def evaluate_new_file_in_dir(
    trigger: TriggerRecord, message_instruction: str, payload: str
) -> NewFileEvalResult:
    """Combined eval + render for directory-scoped triggers on a new file."""
    return nl_evaluate_new_file_in_dir(
        trigger.nl_description, message_instruction, payload
    )


def dispatch(trigger: TriggerRecord, context: dict[str, Any]) -> None:
    """Carry out the trigger action: webhook, external service call, etc."""
    raise NotImplementedError
