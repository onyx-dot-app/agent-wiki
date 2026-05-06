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

from app.wiki.filesystem import parent_dirs


def find_matching_triggers(doc_path: str) -> list[dict]:
    """Return enabled delta triggers attached to this doc or any parent dir."""
    candidates = [doc_path, *parent_dirs(doc_path)]
    # TODO: SELECT from triggers WHERE kind='delta' AND enabled=1 AND scope_path IN candidates
    raise NotImplementedError


def evaluate_delta(trigger: dict, before_body: str, after_body: str) -> bool:
    """LLM check: does this delta satisfy ``trigger['nl_description']``?"""
    # TODO: small LLM call returning yes/no with a one-line justification.
    raise NotImplementedError


def dispatch(trigger: dict, context: dict) -> None:
    """Carry out the trigger action: webhook, external service call, etc."""
    # TODO: switch on trigger['action_json']['kind']: webhook | http | agent_message
    # Record an event row.
    raise NotImplementedError
