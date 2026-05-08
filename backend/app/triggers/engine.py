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

import sqlite3

from app.db.sqlite import connect
from app.triggers.natural_language import (
    evaluate_new_file_in_dir as nl_evaluate_new_file_in_dir,
)
from app.triggers.natural_language import matches as nl_matches
from app.triggers.natural_language import render_message as nl_render_message
from app.wiki.filesystem import parent_dirs


def find_matching_triggers(doc_path: str) -> list[sqlite3.Row]:
    """Return enabled delta triggers attached to ``doc_path`` or any parent dir."""
    candidates = [doc_path, *parent_dirs(doc_path)]
    placeholders = ",".join("?" for _ in candidates)
    conn = connect()
    try:
        rows = conn.execute(
            f"SELECT * FROM triggers "
            f"WHERE kind = 'delta' AND enabled = 1 "
            f"AND scope_path IN ({placeholders})",
            candidates,
        ).fetchall()
        return list(rows)
    finally:
        conn.close()


def evaluate_delta(trigger: sqlite3.Row, payload: str) -> tuple[bool, str]:
    """Phase 1 LLM check: does this delta satisfy ``trigger['nl_description']``?

    ``payload`` is the combined wiki-snapshot + change view from
    ``app.triggers.diff.build_payload``. Returns ``(matched, reason)``;
    ``reason`` is a one-line justification suitable for the events log.
    """
    return nl_matches(trigger["nl_description"], payload)


def render_delta_message(
    message_instruction: str, payload: str, *, reason: str
) -> str:
    """Phase 2 LLM call: render the user-defined message for delivery.

    Only call when ``evaluate_delta`` returned ``matched=True``. Returns
    the final message text to put in the trigger.fire event payload.
    """
    return nl_render_message(message_instruction, payload, reason=reason)


def evaluate_new_file_in_dir(
    trigger: sqlite3.Row, message_instruction: str, payload: str
) -> tuple[bool, str]:
    """Combined eval + render for directory-scoped triggers on a new file.

    Single LLM call returning ``(triggered, trigger_message)`` parsed from
    a JSON object emitted by the model. Used in place of the standard
    two-phase flow when ``change_kind == "create"`` and the trigger's
    scope is a directory rather than the doc itself.
    """
    return nl_evaluate_new_file_in_dir(
        trigger["nl_description"], message_instruction, payload
    )


def dispatch(trigger: dict, context: dict) -> None:
    """Carry out the trigger action: webhook, external service call, etc."""
    # TODO: switch on trigger['action_json']['kind']: webhook | http | agent_message
    # Record an event row.
    raise NotImplementedError
