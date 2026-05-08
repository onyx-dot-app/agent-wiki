"""Post-commit trigger fan-out.

Tasks in this module run on the ``triggers_huey`` queue — the queue
dedicated to natural-language trigger evaluation, both event-driven (this
file) and time-based (``app/tasks/periodic.py:evaluate_scheduled_triggers``).
Trigger eval is read-only (no commits), so it sits on its own queue
between the LLM-heavy ``documents_huey`` and the cheap
``wiki_doc_index_huey``: a flood of trigger fires can't delay an FTS
reindex, and a backlogged doc-updater can't delay an event-log entry.

After a successful ``commit_file`` on a wiki doc, the API (or an agent
tool) enqueues ``fan_out_trigger_eval`` here. It loads BEFORE/AFTER from
git, finds the ``delta`` triggers attached to the doc and its parent
dirs, and routes each one through the appropriate evaluation flow:

* **Standard** (doc-scoped triggers, or directory-scoped on edits) — two
  LLM calls. ``evaluate_delta`` checks if the trigger's NL "if" is
  satisfied; on match, ``render_delta_message`` writes the owner's
  message. Payload: whole-wiki snapshot + +/- diff view.
* **New-file-in-dir** (directory-scoped trigger, ``change_kind ==
  "create"``) — one LLM call. ``evaluate_new_file_in_dir`` returns
  ``(triggered, trigger_message)`` from a single JSON-output prompt. The
  diff view would be noise (every line is a ``+``), so the payload is
  the wiki snapshot + the new file's full body, no diff section.

Each fire becomes one ``trigger.fire`` row in the events table. V0 has
no outbound dispatch (see ``local_data/wiki/natural-language-triggers``).

See ``app/tasks/huey_app.py`` for the queue rationale.
"""
from __future__ import annotations

import json
import logging
import subprocess

from app.db.sqlite import connect
from app.tasks.huey_app import triggers_huey
from app.triggers import diff as diff_helper
from app.triggers.engine import (
    evaluate_delta,
    evaluate_new_file_in_dir,
    find_matching_triggers,
    render_delta_message,
)
from app.wiki import git as wiki_git

log = logging.getLogger(__name__)


@triggers_huey.task()
def fan_out_trigger_eval(
    doc_path: str,
    sha: str,
    change_kind: str,
    actor: str | None = None,
) -> None:
    triggers = find_matching_triggers(doc_path)
    if not triggers:
        log.debug("fan_out_trigger_eval %s: no matching triggers", doc_path)
        return

    log.info(
        "fan_out_trigger_eval %s sha=%s kind=%s candidates=%d",
        doc_path, sha[:8], change_kind, len(triggers),
    )

    after = _read_at(sha, doc_path)
    before = _read_at(f"{sha}^", doc_path)

    # Build the wiki snapshot once and reuse it for every trigger on this
    # commit. Each trigger pays its own per-call eval (and render-on-match).
    wiki_snapshot = diff_helper.build_wiki_snapshot()
    delta_payload = diff_helper.build_payload(
        doc_path=doc_path,
        change_kind=change_kind,
        before=before,
        after=after,
        wiki_snapshot=wiki_snapshot,
    )
    new_file_payload: str | None = None
    if change_kind == "create":
        new_file_payload = diff_helper.build_new_file_payload(
            doc_path=doc_path, body=after, wiki_snapshot=wiki_snapshot
        )

    fired = 0
    for trigger in triggers:
        from app.triggers.repo import _parse_action  # local import avoids cycle
        action = _parse_action(trigger["action_json"])
        instruction = action.get("message") or ""
        destination = action.get("destination")

        if change_kind == "create" and trigger["scope_path"] != doc_path:
            assert new_file_payload is not None
            triggered, rendered = evaluate_new_file_in_dir(
                trigger, instruction, new_file_payload
            )
            if not triggered:
                continue
            reason = "new file under directory scope"
            log.info(
                "trigger fired (new-file-in-dir) id=%s doc=%s",
                trigger["id"], doc_path,
            )
        else:
            matched, reason = evaluate_delta(trigger, delta_payload)
            if not matched:
                continue
            rendered = (
                render_delta_message(instruction, delta_payload, reason=reason)
                if instruction
                else ""
            )
            log.info(
                "trigger fired id=%s doc=%s reason=%s",
                trigger["id"], doc_path, reason,
            )

        fired += 1
        _record_fire(
            trigger=trigger,
            doc_path=doc_path,
            sha=sha,
            change_kind=change_kind,
            reason=reason,
            instruction=instruction,
            rendered_message=rendered,
            destination=destination,
            actor=actor,
        )
    log.info("fan_out_trigger_eval %s: %d/%d fired", doc_path, fired, len(triggers))


def _read_at(ref: str, rel_path: str) -> str:
    """Read ``rel_path`` at git ``ref``. Empty string if missing or parent-less."""
    try:
        return wiki_git.read_file(rel_path, ref=ref)
    except subprocess.CalledProcessError:
        log.debug("read_at miss ref=%s path=%s", ref, rel_path)
        return ""


def _record_fire(
    *,
    trigger: dict,
    doc_path: str,
    sha: str,
    change_kind: str,
    reason: str,
    instruction: str,
    rendered_message: str,
    destination: object,
    actor: str | None,
) -> None:
    """Write a single ``trigger.fire`` row to the events table.

    v0 only supports ``destination = None`` (Event Log delivery); a
    non-null value logs a warning and still records to the Event Log so
    no fire is lost. Outbound dispatch (webhook, agent message, etc.) is
    not implemented yet.
    """
    if destination is not None:
        log.warning(
            "trigger %s has destination=%r but only null (Event Log) is "
            "supported in v0; recording to events anyway",
            trigger["id"], destination,
        )

    event_payload = json.dumps(
        {
            "trigger_id": trigger["id"],
            "doc_path": doc_path,
            "sha": sha,
            "change_kind": change_kind,
            "reason": reason,
            "message": rendered_message,
            "message_instruction": instruction,
            "destination": destination,
        }
    )
    conn = connect()
    try:
        conn.execute(
            "INSERT INTO events(kind, actor, target, payload_json) VALUES (?, ?, ?, ?)",
            ("trigger.fire", actor, trigger["id"], event_payload),
        )
    finally:
        conn.close()
