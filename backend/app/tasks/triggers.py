"""Post-commit trigger fan-out.

Tasks in this module run on the ``triggers_queue`` queue — the queue
dedicated to natural-language trigger evaluation, both event-driven (this
file) and time-based (``app/tasks/periodic.py:evaluate_scheduled_triggers``).
Trigger eval is read-only (no commits), so it sits on its own queue
between the LLM-heavy ``documents_queue`` and the cheap
``lightweight_maintenance_queue``: a flood of trigger fires can't delay
a BM25 reindex, and a backlogged doc-updater can't delay an event-log
entry.

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

Each fire becomes one ``trigger.fire`` row in the events table. After
recording, a ``slack``-destination trigger POSTs the rendered message to
the owner's chosen Slack channel via ``_dispatch_to_slack`` (see
``app/slack/webhooks.py`` and ``app/slack/client.py``); ``event_log`` records
and stops. The event row is always written before any outbound attempt, so a
fire is never lost.

See ``app/tasks/queues.py`` for the queue rationale.
"""
from __future__ import annotations

import json
import logging

from datetime import datetime, timezone

from sqlalchemy import select

from app.db.models import Event, User
from app.db.session import session
from app.slack import client as slack_client
from app.slack import webhooks as slack_webhooks
from app.tasks.queues import triggers_queue
from app.triggers import destinations as destinations_repo
from app.triggers import diff as diff_helper
from app.triggers import repo as triggers_repo
from app.triggers.engine import (
    TriggerRecord,
    evaluate_delta,
    evaluate_new_file_in_dir,
    evaluate_schedule,
    find_due_schedule_triggers,
    find_matching_triggers,
    render_delta_message,
    render_schedule_message,
)
from app.wiki import acl as wiki_acl
from app.wiki import git as wiki_git
from app.models.wiki import ChangeKind

log = logging.getLogger(__name__)


@triggers_queue.task()
def fan_out_trigger_eval(
    doc_path: str,
    sha: str,
    change_kind: ChangeKind,
    actor: str | None = None,
) -> None:
    # Queue round-trip serializes enums to their string values; coerce back.
    change_kind = ChangeKind(change_kind)
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
    if change_kind == ChangeKind.CREATE:
        new_file_payload = diff_helper.build_new_file_payload(
            doc_path=doc_path, body=after, wiki_snapshot=wiki_snapshot
        )

    # Cache owner ACL flags by user id — most fan-outs touch a handful of
    # owners and the same owner often shows up across triggers.
    owner_can_read: dict[str, bool] = {}

    def _owner_can_read(owner_user_id: str) -> bool:
        cached = owner_can_read.get(owner_user_id)
        if cached is not None:
            return cached
        with session() as s:
            row = s.scalars(
                select(User).where(User.id == owner_user_id)
            ).one_or_none()
        if row is None:
            # Orphan trigger (shouldn't normally happen — rebuild
            # disables these). Skip rather than render against a
            # missing principal.
            owner_can_read[owner_user_id] = False
            return False
        allowed = wiki_acl.can(row.id, row.is_admin, "read", doc_path)
        owner_can_read[owner_user_id] = allowed
        return allowed

    fired = 0
    skipped_acl = 0
    for trigger in triggers:
        instruction = trigger.message or ""
        destination = trigger.destination

        # Re-check the owner's read access at fire-time. The trigger may have
        # been created when the owner had access and then revoked; we don't
        # want a rendered message (which embeds doc body excerpts) landing in
        # an event the owner shouldn't have visibility into.
        if not _owner_can_read(trigger.owner_user_id):
            skipped_acl += 1
            log.info(
                "trigger %s skipped: owner %s lacks read on %s",
                trigger.id, trigger.owner_user_id, doc_path,
            )
            continue

        if change_kind == ChangeKind.CREATE and trigger.scope_path != doc_path:
            assert new_file_payload is not None
            new_file_result = evaluate_new_file_in_dir(
                trigger, instruction, new_file_payload
            )
            if not new_file_result.triggered:
                continue
            rendered = new_file_result.message
            reason = "new file under directory scope"
            log.info(
                "trigger fired (new-file-in-dir) id=%s doc=%s",
                trigger.id, doc_path,
            )
        else:
            match = evaluate_delta(trigger, delta_payload)
            if not match.matched:
                continue
            reason = match.reason
            rendered = (
                render_delta_message(instruction, delta_payload, reason=reason)
                if instruction
                else ""
            )
            log.info(
                "trigger fired id=%s doc=%s reason=%s",
                trigger.id, doc_path, reason,
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
    log.info(
        "fan_out_trigger_eval %s: %d/%d fired (%d skipped on owner ACL)",
        doc_path, fired, len(triggers), skipped_acl,
    )


def evaluate_due_schedule_triggers(now: datetime) -> int:
    """Evaluate every schedule trigger whose next cron fire is ≤ ``now``.

    Returns the number that fired (matched). Called by the
    ``evaluate_scheduled_triggers`` periodic task. Each trigger gets its
    own owner-ACL re-check + LLM gate, mirroring the delta path. Always
    advances ``schedule_last_fired_at`` to ``now`` regardless of
    match/skip — otherwise the same tick re-evaluates next pass.
    """
    triggers = find_due_schedule_triggers(now)
    if not triggers:
        log.debug("schedule eval: no due triggers")
        return 0

    log.info("schedule eval: %d due trigger(s)", len(triggers))
    wiki_snapshot = diff_helper.build_wiki_snapshot()
    now_iso = now.astimezone(timezone.utc).isoformat(timespec="seconds")

    fired = 0
    for trigger in triggers:
        try:
            if _evaluate_one_schedule(trigger, now_iso=now_iso, wiki_snapshot=wiki_snapshot):
                fired += 1
        finally:
            # Always advance last_fired_at, even on no-match or exception,
            # so the next tick doesn't re-evaluate the same window.
            triggers_repo.record_schedule_fire(trigger.id, now_iso)
    log.info("schedule eval: %d/%d fired", fired, len(triggers))
    return fired


def _evaluate_one_schedule(
    trigger: TriggerRecord,
    *,
    now_iso: str,
    wiki_snapshot: str,
) -> bool:
    """Evaluate a single schedule trigger; record a ``trigger.fire`` event
    on match. Returns True if it fired.
    """
    if not _owner_can_read_scope(trigger):
        log.info(
            "schedule trigger %s skipped: owner %s lacks read on %s",
            trigger.id, trigger.owner_user_id, trigger.scope_path,
        )
        return False

    payload = diff_helper.build_schedule_payload(
        scope_path=trigger.scope_path,
        when_iso=now_iso,
        wiki_snapshot=wiki_snapshot,
    )
    match = evaluate_schedule(trigger, payload)
    if not match.matched:
        return False

    instruction = trigger.message or ""
    rendered = (
        render_schedule_message(instruction, payload, reason=match.reason)
        if instruction
        else ""
    )
    log.info(
        "schedule trigger fired id=%s scope=%s reason=%s",
        trigger.id, trigger.scope_path, match.reason,
    )
    _record_fire(
        trigger=trigger,
        doc_path=trigger.scope_path,
        sha="",
        change_kind=ChangeKind.SCHEDULE,
        reason=match.reason,
        instruction=instruction,
        rendered_message=rendered,
        destination=trigger.destination,
        actor=None,
    )
    return True


def _owner_can_read_scope(trigger: TriggerRecord) -> bool:
    """Re-check the owner's read access against the trigger's scope at
    fire-time, mirroring the delta path.

    Same semantics as the inline check in ``fan_out_trigger_eval``: an
    owner whose access was revoked after creation shouldn't fire a
    rendered message that embeds doc body excerpts.
    """
    with session() as s:
        user_row = s.scalars(
            select(User).where(User.id == trigger.owner_user_id)
        ).one_or_none()
    if user_row is None:
        return False
    return wiki_acl.can(user_row.id, user_row.is_admin, "read", trigger.scope_path)


def _read_at(ref: str, rel_path: str) -> str:
    """Read ``rel_path`` at git ``ref``. Empty string if missing or parent-less."""
    try:
        return wiki_git.read_file(rel_path, ref=ref)
    except wiki_git.UnknownSha:
        log.debug("read_at miss ref=%s path=%s", ref, rel_path)
        return ""


def _record_fire(
    *,
    trigger: TriggerRecord,
    doc_path: str,
    sha: str,
    change_kind: ChangeKind,
    reason: str,
    instruction: str,
    rendered_message: str,
    destination: object,
    actor: str | None,
) -> None:
    """Record a ``trigger.fire`` row, then dispatch outbound if configured.

    The events row is always written first, so a fire is never lost even
    if outbound delivery fails or the destination has no dispatcher. After
    recording, ``slack`` destinations POST the rendered message to the
    admin-configured incoming webhook; an unknown destination just logs a
    warning. (``event_log`` records and stops — that's the whole delivery.)
    """
    event_payload = json.dumps(
        {
            "trigger_id": trigger.id,
            "doc_path": doc_path,
            "sha": sha,
            "change_kind": change_kind,
            "reason": reason,
            "message": rendered_message,
            "message_instruction": instruction,
            "destination": destination,
        }
    )
    with session() as s:
        s.add(
            Event(
                kind="trigger.fire",
                actor=actor,
                target=trigger.id,
                payload_json=event_payload,
            )
        )

    if destination == destinations_repo.SLACK_ID:
        _dispatch_to_slack(trigger=trigger, rendered_message=rendered_message)
    elif destination != destinations_repo.EVENT_LOG_ID:
        log.warning(
            "trigger %s has destination=%r but no outbound dispatcher is "
            "wired up; recorded to events only",
            trigger.id, destination,
        )


def _dispatch_to_slack(*, trigger: TriggerRecord, rendered_message: str) -> None:
    """POST a fire's rendered message to the trigger's Slack channel.

    Resolves ``trigger.slack_webhook_id`` to its owner's webhook URL. No-op
    when the trigger has no channel set or the webhook was deleted/not owned.
    Failures are logged and swallowed — the fire is already recorded in the
    events table, so an unreachable Slack must not fail the task or lose it.
    """
    if not trigger.slack_webhook_id:
        log.info(
            "trigger %s targets slack but has no channel set; recorded to events only",
            trigger.id,
        )
        return

    webhook_url = slack_webhooks.get_url(
        trigger.slack_webhook_id, owner_user_id=trigger.owner_user_id
    )
    if not webhook_url:
        log.info(
            "trigger %s slack channel %s missing/not owned; recorded to events only",
            trigger.id, trigger.slack_webhook_id,
        )
        return

    if not rendered_message.strip():
        log.info("trigger %s slack dispatch skipped: empty message", trigger.id)
        return

    try:
        slack_client.post_message(webhook_url=webhook_url, text=rendered_message)
        log.info("trigger %s dispatched to slack channel %s", trigger.id, trigger.slack_webhook_id)
    except slack_client.SlackApiError:
        log.exception("trigger %s slack dispatch failed", trigger.id)
