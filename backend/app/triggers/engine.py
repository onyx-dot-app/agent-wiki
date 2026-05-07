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
from app.triggers.natural_language import matches as nl_matches
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


def evaluate_delta(
    trigger: sqlite3.Row,
    before_body: str,
    after_body: str,
    *,
    change_kind: str,
) -> tuple[bool, str]:
    """LLM check: does this delta satisfy ``trigger['nl_description']``?

    Returns ``(matched, reason)``. ``reason`` is a one-line justification from
    the model, suitable for the events log.
    """
    return nl_matches(
        trigger["nl_description"],
        before_body,
        after_body,
        change_kind=change_kind,
    )


def dispatch(trigger: dict, context: dict) -> None:
    """Carry out the trigger action: webhook, external service call, etc."""
    # TODO: switch on trigger['action_json']['kind']: webhook | http | agent_message
    # Record an event row.
    raise NotImplementedError
