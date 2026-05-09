"""Trigger evaluation.

Two flavors:
  * **delta**     — fires when a doc (or any doc under a directory scope) gets
                    a meaningful update. Uses an LLM check against the
                    ``nl_description`` of the trigger.
  * **schedule**  — fires on cron, evaluated by the periodic task.

When a doc is updated we evaluate triggers whose ``scope_path`` matches the
doc itself or any parent directory.
"""
from __future__ import annotations

from typing import Any

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
from app.triggers.natural_language import render_message as nl_render_message
from app.triggers.repo import _parse_action  # pyright: ignore[reportPrivateUsage]
from app.wiki.filesystem import parent_dirs


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
    enabled: bool
    file_path: str | None
    created_at: str | None
    last_edited_at: str | None


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
        enabled=t.enabled,
        file_path=t.file_path,
        created_at=t.created_at,
        last_edited_at=t.last_edited_at,
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
