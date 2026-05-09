"""Trigger CRUD.

Owner-scoped: a user only sees and mutates the triggers they own. v0 only
honors ``kind=delta``; the schema supports ``schedule`` but the eval path
isn't wired yet so we reject it at the API boundary.

Storage is git-backed YAML — see ``app/triggers/storage.py``. Postgres is
a cache populated by ``app/triggers/repo.py``.
"""
from __future__ import annotations

import logging
import re
from typing import Any

from flask import Blueprint, jsonify, request

from app.auth import current_user, login_required
from app.models._helpers import error, parse_body
from app.models.trigger import (
    CreateTriggerRequest,
    TriggerCommit,
    TriggerHistoryResponse,
    TriggerListResponse,
    TriggerVersionResponse,
    TriggerView,
    UpdateTriggerRequest,
)
from app.triggers import repo as triggers_repo
from app.triggers import storage as triggers_storage
from app.wiki import git as wiki_git

bp = Blueprint("triggers", __name__)
log = logging.getLogger(__name__)

_SHA_RE = re.compile(r"^[0-9a-f]{4,40}$")


def _git_author() -> str | None:
    user = current_user()
    if not user:
        return None
    return f"{user.name or user.email} <{user.email}>"


def _normalize_scope_path(raw: str) -> str:
    """Normalize a user-supplied scope path. Raises ``ValueError`` with a
    user-facing message if the value is missing or invalid."""
    if not raw.strip():
        raise ValueError("scope_path is required")
    return triggers_storage.normalize_scope_path(raw)


def _to_view(row: dict[str, Any]) -> TriggerView:
    return TriggerView.model_validate(row)


@bp.get("")
@login_required
def list_triggers():
    user = current_user()
    assert user is not None
    rows = triggers_repo.list_for_owner(user.id)
    return jsonify(TriggerListResponse(
        triggers=[_to_view(r) for r in rows],
    ).model_dump())


@bp.post("")
@login_required
def create_trigger():
    user = current_user()
    assert user is not None
    req = parse_body(CreateTriggerRequest, request.get_json(silent=True))

    try:
        scope_path = _normalize_scope_path(req.scope_path)
    except ValueError as exc:
        return error(str(exc), 400)

    if req.destination not in triggers_repo.SUPPORTED_DESTINATIONS:
        return error(f"destination {req.destination!r} not supported in v0 — only null (Event Log)", 400)

    if req.kind not in triggers_repo.ALLOWED_KINDS:
        return error(f"kind {req.kind!r} not supported in v0", 400)

    try:
        trigger = triggers_repo.create(
            owner_user_id=user.id,
            scope_path=scope_path,
            nl_description=req.nl_description.strip(),
            message=req.message.strip(),
            destination=req.destination,
            kind=req.kind,
            enabled=req.enabled,
            actor=_git_author(),
        )
    except ValueError as exc:
        return error(str(exc), 400)
    log.info(
        "trigger created id=%s owner=%s scope=%s kind=%s enabled=%s",
        trigger.get("id"), user.id, scope_path, req.kind, req.enabled,
    )
    return jsonify(_to_view(trigger).model_dump()), 201


@bp.put("/<trigger_id>")
@login_required
def update_trigger(trigger_id: str):
    user = current_user()
    assert user is not None
    existing = triggers_repo.get(trigger_id)
    if existing is None:
        return error("not found", 404)
    if existing["owner_user_id"] != user.id:
        return error("forbidden", 403)

    raw: dict[str, Any] = request.get_json(silent=True) or {}
    req = parse_body(UpdateTriggerRequest, raw)
    kwargs: dict[str, Any] = {}

    if "scope_path" in raw:
        try:
            kwargs["scope_path"] = _normalize_scope_path(req.scope_path or "")
        except ValueError as exc:
            return error(str(exc), 400)

    if "nl_description" in raw:
        nl = (req.nl_description or "").strip()
        if not nl:
            return error("nl_description cannot be empty", 400)
        kwargs["nl_description"] = nl

    if "message" in raw:
        msg = (req.message or "").strip()
        if not msg:
            return error("message cannot be empty", 400)
        kwargs["message"] = msg

    if "destination" in raw:
        if req.destination not in triggers_repo.SUPPORTED_DESTINATIONS:
            return error(f"destination {req.destination!r} not supported in v0 — only null (Event Log)", 400)
        kwargs["destination"] = req.destination

    if "enabled" in raw:
        kwargs["enabled"] = req.enabled

    try:
        updated = triggers_repo.update(trigger_id, actor=_git_author(), **kwargs)
    except ValueError as exc:
        return error(str(exc), 400)
    if updated is None:
        return error("not found", 404)
    log.info(
        "trigger updated id=%s owner=%s fields=%s",
        trigger_id, user.id, sorted(kwargs.keys()),
    )
    return jsonify(_to_view(updated).model_dump())


@bp.delete("/<trigger_id>")
@login_required
def delete_trigger(trigger_id: str):
    user = current_user()
    assert user is not None
    existing = triggers_repo.get(trigger_id)
    if existing is None:
        return error("not found", 404)
    if existing["owner_user_id"] != user.id:
        return error("forbidden", 403)
    triggers_repo.delete(trigger_id, actor=_git_author())
    log.info("trigger deleted id=%s owner=%s", trigger_id, user.id)
    return ("", 204)


@bp.get("/<trigger_id>/history")
@login_required
def trigger_history(trigger_id: str):
    """Git history for the trigger's YAML file (config edits, not fires).

    Fire history lives in the events table — see ``GET /api/events``.
    """
    user = current_user()
    assert user is not None
    existing = triggers_repo.get(trigger_id)
    if existing is None:
        return error("not found", 404)
    if existing["owner_user_id"] != user.id:
        return error("forbidden", 403)
    file_path = existing.get("file_path")
    if not file_path:
        return jsonify(TriggerHistoryResponse(commits=[]).model_dump())
    commits = [TriggerCommit(**c.model_dump()) for c in wiki_git.history(file_path)]
    return jsonify(TriggerHistoryResponse(commits=commits).model_dump())


@bp.get("/<trigger_id>/version/<sha>")
@login_required
def trigger_version(trigger_id: str, sha: str):
    """Read the trigger's fields as they existed at a specific commit.

    Used by the UI to populate the edit form with a historical version. Saving
    from there goes through the normal PUT and creates a new commit.
    """
    if not _SHA_RE.match(sha):
        return error("invalid sha", 400)
    user = current_user()
    assert user is not None
    existing = triggers_repo.get(trigger_id)
    if existing is None:
        return error("not found", 404)
    if existing["owner_user_id"] != user.id:
        return error("forbidden", 403)

    path = triggers_storage.find_path_at_sha(trigger_id, sha)
    if not path:
        return error("trigger not present at that revision", 404)
    try:
        data = triggers_storage.read_trigger_at(path, sha)
    except Exception:
        log.exception("failed to read trigger %s at %s", trigger_id, sha)
        return error("failed to read version", 500)

    return jsonify(TriggerVersionResponse(
        scope_path=data.get("scope_path"),
        nl_description=data.get("nl_description"),
        message=data.get("message"),
        destination=data.get("destination"),
        enabled=bool(data.get("enabled", True)),
        sha=sha,
        path=path,
    ).model_dump())
