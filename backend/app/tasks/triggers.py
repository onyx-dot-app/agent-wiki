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
import re
from typing import Any, cast

from datetime import datetime, timezone

from sqlalchemy import select

from app.db.models import Event, User
from app.email import service as email_service
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
    matched_scope,
    render_delta_message,
    render_schedule_message,
    schedule_window_start,
)
from app.wiki import acl as wiki_acl
from app.wiki.links import doc_url
from app.wiki import git as wiki_git
from app.models.webhook import WebhookEvent
from app.net.ssrf import UnsafeUrlError
from app.webhooks import client as webhook_client
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

    # Payloads are scoped per trigger: each carries only the docs under that
    # trigger's scope plus the shared change view. One scope block is built
    # per distinct scope on this commit.
    scope_blocks: dict[str, str] = {}

    def _scope_block(scope_path: str) -> str:
        block = scope_blocks.get(scope_path)
        if block is None:
            block = diff_helper.build_scope_block(scope_path)
            scope_blocks[scope_path] = block
        return block

    def _lead_block(trigger: TriggerRecord) -> str:
        """The payload's scoped-docs lead covering the trigger's whole watch
        list; per-path blocks are shared across triggers on this commit."""
        paths = list(dict.fromkeys(s.path for s in trigger.scopes))
        return "\n\n".join(_scope_block(p) for p in paths)

    def _delta_payload(trigger: TriggerRecord) -> str:
        return diff_helper.build_payload(
            doc_path=doc_path,
            change_kind=change_kind,
            before=before,
            after=after,
            scope_path=trigger.scope_path,
            scope_block=_lead_block(trigger),
        )

    def _new_file_payload(trigger: TriggerRecord) -> str:
        return diff_helper.build_new_file_payload(
            doc_path=doc_path,
            body=after,
            scope_path=trigger.scope_path,
            scope_block=_lead_block(trigger),
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

        # A line-ranged watch entry only fires when the edit touches those
        # lines of the changed doc; checked cheaply before any LLM eval.
        matched = matched_scope(trigger, doc_path)
        if matched is not None and matched.start_line is not None:
            end = matched.end_line or matched.start_line
            if not diff_helper.change_touches_lines(
                before, after, matched.start_line, end
            ):
                log.debug(
                    "trigger %s skipped: change outside lines %d-%d of %s",
                    trigger.id, matched.start_line, end, doc_path,
                )
                continue

        # Evaluate the firing condition once per trigger, then deliver each
        # action. Delta renders per action; the new-file eval renders in its
        # single combined call.
        matched_path = matched.path if matched is not None else trigger.scope_path
        if change_kind == ChangeKind.CREATE and matched_path != doc_path:
            primary = (trigger.actions[0].message or "") if trigger.actions else ""
            new_file_result = evaluate_new_file_in_dir(
                trigger, primary, _new_file_payload(trigger)
            )
            if not new_file_result.triggered:
                continue
            new_file_message: str | None = new_file_result.message
            reason = "new file under directory scope"
            log.info("trigger fired (new-file-in-dir) id=%s doc=%s", trigger.id, doc_path)
        else:
            match = evaluate_delta(trigger, _delta_payload(trigger))
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
                    render_delta_message(
                        instruction, _delta_payload(trigger), reason=reason
                    )
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
    now_iso = now.astimezone(timezone.utc).isoformat(timespec="seconds")

    # One scope block per distinct scope this tick — due triggers often share one.
    scope_blocks: dict[str, str] = {}

    fired = 0
    for trigger in triggers:
        try:
            since_iso = schedule_window_start(trigger, now).isoformat(timespec="seconds")
            scope = trigger.scope_path
            if scope not in scope_blocks:
                scope_blocks[scope] = diff_helper.build_scope_block(scope)
            if _evaluate_one_schedule(
                trigger,
                now_iso=now_iso,
                since_iso=since_iso,
                scope_block=scope_blocks[scope],
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
    scope_block: str,
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
        scope_block=scope_block,
        when_iso=now_iso,
        since_iso=since_iso,
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
            "destination_name": config["name"] if config else None,
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
    elif dtype == destinations_repo.EMAIL_ID:
        _dispatch_to_email(
            trigger=trigger,
            config=config,
            doc_path=doc_path,
            rendered_message=rendered_message,
        )
    elif dtype == destinations_repo.WEBHOOK_ID:
        _dispatch_to_webhook(
            trigger=trigger,
            config=config,
            doc_path=doc_path,
            sha=sha,
            change_kind=change_kind,
            reason=reason,
            rendered_message=rendered_message,
            actor=actor,
        )
    else:
        log.warning(
            "trigger %s destination type %r has no outbound dispatcher; recorded to events only",
            trigger.id, dtype,
        )


def _str_map_or_none(obj: object) -> dict[str, str] | None:
    """Coerce a config value to a string->string map, or None if it isn't a
    dict (config validation already guarantees string entries)."""
    if not isinstance(obj, dict):
        return None
    return {str(k): str(v) for k, v in cast("dict[object, object]", obj).items()}


def _dispatch_to_webhook(
    *,
    trigger: TriggerRecord,
    config: dict[str, Any],
    doc_path: str,
    sha: str,
    change_kind: ChangeKind,
    reason: str,
    rendered_message: str,
    actor: str | None,
) -> None:
    """POST the fire as a structured event to the config's webhook URL.

    Workflow destinations get machine-readable fields, not the prose message.
    The URL, custom headers, routing tag, and static fields come from the
    config; the signing secret HMACs the body. Failures are logged and
    swallowed: the fire is already recorded.
    """
    target = cast("dict[str, Any]", config.get("config") or {})
    url = target.get("url")
    if not isinstance(url, str) or not url:
        log.warning(
            "trigger %s webhook config %s has no url; recorded to events only",
            trigger.id, config["id"],
        )
        return
    event = WebhookEvent(
        trigger_id=trigger.id,
        trigger_kind=trigger.kind,
        doc_path=doc_path,
        sha=sha,
        change_kind=change_kind.value,
        fired_at=datetime.now(timezone.utc).isoformat(),
        actor=actor,
        summary=rendered_message,
        reason=reason,
        routing_tag=target.get("routing_tag"),
        fields=_str_map_or_none(target.get("fields")) or {},
    )
    body = event.model_dump_json().encode()
    headers = _str_map_or_none(target.get("headers"))
    secret = dest_configs.get_secret(
        str(config["id"]), owner_user_id=trigger.owner_user_id
    )
    try:
        webhook_client.deliver(url=url, body=body, headers=headers, secret=secret)
        log.info("trigger %s dispatched to webhook config %s", trigger.id, config["id"])
    except (webhook_client.WebhookError, UnsafeUrlError):
        # An unsafe URL (e.g. DNS rebinding to a private host at fire time) raises
        # UnsafeUrlError, not WebhookError. Swallow both so the recorded fire stands.
        log.exception("trigger %s webhook dispatch failed", trigger.id)


def _dispatch_to_email(
    *, trigger: TriggerRecord, config: dict[str, object], doc_path: str, rendered_message: str
) -> None:
    """Deliver a fire's rendered message to the config's verified address.

    Unverified addresses are skipped — verification is what stops the wiki
    being used to mail strangers. Failures are logged and swallowed: the
    fire is already recorded in the events table.
    """
    if not rendered_message.strip():
        log.info("trigger %s email dispatch skipped: empty message", trigger.id)
        return
    if not config.get("verified_at"):
        log.info(
            "trigger %s email config %s unverified; recorded to events only",
            trigger.id, config["id"],
        )
        return
    address = str(cast("dict[str, Any]", config.get("config") or {}).get("address") or "")
    if not address:
        log.warning("trigger %s email config %s has no address", trigger.id, config["id"])
        return
    doc_link = doc_url(doc_path)
    text = (
        f"{rendered_message}\n\n— Agent Wiki trigger on {doc_path}\n{doc_link}"
    )
    try:
        email_service.send(
            to=address,
            subject=f"Agent Wiki: {doc_path}",
            text=text,
        )
        log.info("trigger %s dispatched to email config %s", trigger.id, config["id"])
    except email_service.EmailSendError:
        log.exception("trigger %s email dispatch failed", trigger.id)


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
        # Slack webhook URLs embed their workspace id
        # (hooks.slack.com/services/<TEAM>/...), so the mute check targets
        # exactly the webhook's own workspace. For URLs that don't parse:
        # a single connection is the only Slack there is, so its mute
        # applies; with multiple workspaces an unattributable webhook never
        # borrows another workspace's mute and keeps sending.
        team_match = re.match(
            r"https://hooks\.slack\.com/services/(T[A-Z0-9]+)/", webhook_url
        )
        connections = slack_connections.list_for_user(trigger.owner_user_id)
        muted_team = None
        if team_match:
            muted_team = next(
                (
                    c["team_id"]
                    for c in connections
                    if c["team_id"] == team_match.group(1) and c.get("muted")
                ),
                None,
            )
        elif len(connections) == 1 and connections[0].get("muted"):
            muted_team = connections[0]["team_id"]
        if muted_team is not None:
            log.info(
                "trigger %s slack webhook suppressed: connection %s is muted; "
                "recorded to events only",
                trigger.id, muted_team,
            )
            return
        try:
            slack_client.post_message(
                webhook_url=webhook_url, text=slack_client.to_mrkdwn(rendered_message)
            )
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

    # Resolve the connection the target belongs to. Configs stamped with a
    # team_id use exactly that workspace. Legacy unstamped configs try each
    # of the owner's connections in the stable order until one accepts the
    # channel (a wrong-workspace token fails without delivering), so the
    # channel's real workspace wins rather than whichever row happens to be
    # first. Mute is checked on the SAME connection that would deliver.
    config_team = target.get("team_id")
    if config_team:
        stamped = slack_connections.get(trigger.owner_user_id, str(config_team))
        candidates = [stamped] if stamped else []
    else:
        candidates = slack_connections.list_for_user(trigger.owner_user_id)
    if not candidates:
        log.info(
            "trigger %s owner %s has no slack connection for this target; "
            "recorded to events only",
            trigger.id, trigger.owner_user_id,
        )
        return
    unmuted = [c for c in candidates if not c.get("muted")]
    if not unmuted:
        log.info(
            "trigger %s slack connections muted for this target; "
            "recorded to events only",
            trigger.id,
        )
        return

    for connection in unmuted:
        bot_token = slack_connections.get_bot_token(
            trigger.owner_user_id, str(connection["team_id"])
        )
        if not bot_token:
            continue
        # Channel posts name the owner as a real mention; a DM already is
        # the owner.
        source = f"— Agent Wiki trigger on {doc_path}"
        if not wants_dm:
            source += f", for <@{connection['slack_user_id']}>"
        text = f"{slack_client.to_mrkdwn(rendered_message)}\n{source}"
        try:
            post_channel = channel_id
            if wants_dm and not post_channel:
                post_channel = slack_client.open_dm(
                    bot_token=bot_token,
                    slack_user_id=str(connection["slack_user_id"]),
                )
            slack_client.post_chat_message(
                bot_token=bot_token, channel=str(post_channel), text=text
            )
            log.info(
                "trigger %s dispatched via slack bot config %s team %s",
                trigger.id, config_id, connection["team_id"],
            )
            return
        except slack_client.SlackApiError:
            log.warning(
                "trigger %s slack post failed on team %s; trying next connection",
                trigger.id, connection["team_id"], exc_info=True,
            )
    log.error(
        "trigger %s slack bot dispatch failed on all connections; "
        "recorded to events only",
        trigger.id,
    )
