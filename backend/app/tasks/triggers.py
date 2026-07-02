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
from typing import Any, cast

from datetime import datetime, timezone

from sqlalchemy import select

from app.db.models import Event, User
from app.db.session import session
from app.slack import client as slack_client
from app.slack import connections as slack_connections
from app.tasks.queues import triggers_queue
from app.triggers import destination_configs as dest_configs
from app.triggers import destinations as destinations_repo
from app.triggers import diff as diff_helper
from app.triggers import repo as triggers_repo
from app.triggers.engine import (
    TriggerAction,
    TriggerRecord,
    evaluate_delta,
    evaluate_new_file_in_dir,
    evaluate_schedule,
    find_due_schedule_triggers,
    find_matching_triggers,
    render_delta_message,
    render_schedule_message,
    schedule_window_start,
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

        # Evaluate the firing condition once per trigger, then deliver each
        # action. Delta renders per action; the new-file eval renders in its
        # single combined call.
        if change_kind == ChangeKind.CREATE and trigger.scope_path != doc_path:
            assert new_file_payload is not None
            primary = (trigger.actions[0].message or "") if trigger.actions else ""
            new_file_result = evaluate_new_file_in_dir(
                trigger, primary, new_file_payload
            )
            if not new_file_result.triggered:
                continue
            new_file_message: str | None = new_file_result.message
            reason = "new file under directory scope"
            log.info("trigger fired (new-file-in-dir) id=%s doc=%s", trigger.id, doc_path)
        else:
            match = evaluate_delta(trigger, delta_payload)
            if not match.matched:
                continue
            new_file_message = None
            reason = match.reason
            log.info("trigger fired id=%s doc=%s reason=%s", trigger.id, doc_path, reason)

        fired += 1
        for action in trigger.actions:
            instruction = action.message or ""
            if new_file_message is not None:
                rendered = new_file_message
            else:
                rendered = (
                    render_delta_message(instruction, delta_payload, reason=reason)
                    if instruction
                    else ""
                )
            _record_fire(
                trigger=trigger,
                action=action,
                doc_path=doc_path,
                sha=sha,
                change_kind=change_kind,
                reason=reason,
                instruction=instruction,
                rendered_message=rendered,
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
            since_iso = schedule_window_start(trigger, now).isoformat(timespec="seconds")
            if _evaluate_one_schedule(
                trigger,
                now_iso=now_iso,
                since_iso=since_iso,
                wiki_snapshot=wiki_snapshot,
            ):
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
    since_iso: str,
    wiki_snapshot: str,
) -> bool:
    """Evaluate a single schedule trigger; record a ``trigger.fire`` event
    on match. Returns True if it fired.

    ``since_iso`` bounds the "changes since last check" diff window (the
    previous tick / last fire — see ``schedule_window_start``).
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
        since_iso=since_iso,
        wiki_snapshot=wiki_snapshot,
    )
    match = evaluate_schedule(trigger, payload)
    if not match.matched:
        return False

    log.info(
        "schedule trigger fired id=%s scope=%s reason=%s",
        trigger.id, trigger.scope_path, match.reason,
    )
    for action in trigger.actions:
        instruction = action.message or ""
        rendered = (
            render_schedule_message(instruction, payload, reason=match.reason)
            if instruction
            else ""
        )
        _record_fire(
            trigger=trigger,
            action=action,
            doc_path=trigger.scope_path,
            sha="",
            change_kind=ChangeKind.SCHEDULE,
            reason=match.reason,
            instruction=instruction,
            rendered_message=rendered,
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
    action: TriggerAction,
    doc_path: str,
    sha: str,
    change_kind: ChangeKind,
    reason: str,
    instruction: str,
    rendered_message: str,
    actor: str | None,
) -> None:
    """Record a ``trigger.fire`` row, then dispatch outbound if configured.

    The events row is always written first, so a fire is never lost even if
    outbound delivery fails. The action's ``destination_config_id`` is resolved
    through ``destination_configs`` and delivered by the config's type. A null
    id (event-log) records and stops.
    """
    config_id = action.destination_config_id
    config = dest_configs.get(config_id, trigger.owner_user_id) if config_id else None
    dtype = config["type"] if config else ("event_log" if config_id is None else "unknown")
    event_payload = json.dumps(
        {
            "trigger_id": trigger.id,
            "doc_path": doc_path,
            "sha": sha,
            "change_kind": change_kind,
            "reason": reason,
            "message": rendered_message,
            "message_instruction": instruction,
            "destination_config_id": config_id,
            "destination_type": dtype,
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

    if config is None:
        # None is event-log only; a set-but-missing config was deleted or is not
        # owned, so record the fire and skip outbound.
        if config_id is not None:
            log.info(
                "trigger %s destination config %s missing or not owned; recorded to events only",
                trigger.id, config_id,
            )
        return
    assert config_id is not None  # config resolved only when config_id is set
    if dtype == destinations_repo.SLACK_ID:
        _dispatch_to_slack(
            trigger=trigger,
            config=config,
            doc_path=doc_path,
            rendered_message=rendered_message,
        )
    else:
        log.warning(
            "trigger %s destination type %r has no outbound dispatcher; recorded to events only",
            trigger.id, dtype,
        )


def _dispatch_to_slack(
    *, trigger: TriggerRecord, config: dict[str, object], doc_path: str, rendered_message: str
) -> None:
    """Deliver a fire's rendered message to the config's Slack target.

    Three target shapes: a legacy incoming webhook (the config's encrypted
    secret), a bot channel (``config.channel_id``), or a DM to the owner
    (``config.dm``). Bot targets post through the owner's Slack connection
    and carry a source line so the recipient can place the message. Failures
    are logged and swallowed: the fire is already recorded in the events
    table, so an unreachable Slack must not fail the task or lose it.
    """
    if not rendered_message.strip():
        log.info("trigger %s slack dispatch skipped: empty message", trigger.id)
        return
    config_id = str(config["id"])

    if config.get("has_secret"):
        webhook_url = dest_configs.get_secret(config_id, owner_user_id=trigger.owner_user_id)
        if not webhook_url:
            log.info(
                "trigger %s slack config %s secret unavailable; recorded to events only",
                trigger.id, config_id,
            )
            return
        try:
            slack_client.post_message(webhook_url=webhook_url, text=rendered_message)
            log.info("trigger %s dispatched to slack webhook config %s", trigger.id, config_id)
        except slack_client.SlackApiError:
            log.exception("trigger %s slack webhook dispatch failed", trigger.id)
        return

    target = cast("dict[str, Any]", config.get("config") or {})
    channel_id = target.get("channel_id")
    wants_dm = bool(target.get("dm"))
    if not channel_id and not wants_dm:
        log.warning(
            "trigger %s slack config %s has no delivery target; recorded to events only",
            trigger.id, config_id,
        )
        return

    connection = next(iter(slack_connections.list_for_user(trigger.owner_user_id)), None)
    if connection is None:
        log.info(
            "trigger %s owner %s has no slack connection; recorded to events only",
            trigger.id, trigger.owner_user_id,
        )
        return
    bot_token = slack_connections.get_bot_token(
        trigger.owner_user_id, str(connection["team_id"])
    )
    if not bot_token:
        log.info(
            "trigger %s slack connection token unavailable; recorded to events only",
            trigger.id,
        )
        return

    # Channel posts name the owner as a real mention; a DM already is the owner.
    source = f"— Agent Wiki trigger on {doc_path}"
    if not wants_dm:
        source += f", for <@{connection['slack_user_id']}>"
    text = f"{rendered_message}\n{source}"
    try:
        if wants_dm and not channel_id:
            channel_id = slack_client.open_dm(
                bot_token=bot_token, slack_user_id=str(connection["slack_user_id"])
            )
        slack_client.post_chat_message(
            bot_token=bot_token, channel=str(channel_id), text=text
        )
        log.info("trigger %s dispatched via slack bot config %s", trigger.id, config_id)
    except slack_client.SlackApiError:
        log.exception("trigger %s slack bot dispatch failed", trigger.id)
