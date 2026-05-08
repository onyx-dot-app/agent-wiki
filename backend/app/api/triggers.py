"""Trigger CRUD.

Owner-scoped: a user only sees and mutates the triggers they own. v0 only
honors ``kind=delta``; the schema supports ``schedule`` but the eval path
isn't wired yet so we reject it at the API boundary.

Storage is git-backed YAML — see ``app/triggers/storage.py``. SQLite is a
cache populated by ``app/triggers/repo.py``.
"""
from __future__ import annotations

import logging
import re

from flask import Blueprint, jsonify, request

from app.auth import current_user, login_required
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


def _validate_scope_path(raw: str) -> tuple[str | None, str | None]:
    if not isinstance(raw, str) or not raw.strip():
        return None, "scope_path is required"
    try:
        rel = triggers_storage.normalize_scope_path(raw)
    except ValueError as e:
        return None, str(e)
    return rel, None


@bp.get("")
@login_required
def list_triggers():
    user = current_user()
    rows = triggers_repo.list_for_owner(user.id)
    return jsonify(triggers=rows)


@bp.post("")
@login_required
def create_trigger():
    user = current_user()
    data = request.get_json(silent=True) or {}

    scope_path, err = _validate_scope_path(data.get("scope_path", ""))
    if err:
        return jsonify(error=err), 400

    nl = (data.get("nl_description") or "").strip()
    if not nl:
        return jsonify(error="nl_description is required"), 400

    message = (data.get("message") or "").strip()
    if not message:
        return jsonify(error="message is required"), 400

    destination = data.get("destination", None)
    if destination not in triggers_repo.SUPPORTED_DESTINATIONS:
        return jsonify(
            error=f"destination {destination!r} not supported in v0 — only null (Event Log)"
        ), 400

    kind = data.get("kind", "delta")
    if kind not in triggers_repo.ALLOWED_KINDS:
        return jsonify(error=f"kind {kind!r} not supported in v0"), 400

    enabled = data.get("enabled", True)
    if not isinstance(enabled, bool):
        return jsonify(error="enabled must be a boolean"), 400

    try:
        trigger = triggers_repo.create(
            owner_user_id=user.id,
            scope_path=scope_path,
            nl_description=nl,
            message=message,
            destination=destination,
            kind=kind,
            enabled=enabled,
            actor=_git_author(),
        )
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    log.info(
        "trigger created id=%s owner=%s scope=%s kind=%s enabled=%s",
        trigger.get("id"), user.id, scope_path, kind, enabled,
    )
    return jsonify(trigger), 201


@bp.put("/<trigger_id>")
@login_required
def update_trigger(trigger_id: str):
    user = current_user()
    existing = triggers_repo.get(trigger_id)
    if existing is None:
        return jsonify(error="not found"), 404
    if existing["owner_user_id"] != user.id:
        return jsonify(error="forbidden"), 403

    data = request.get_json(silent=True) or {}
    kwargs: dict = {}

    if "scope_path" in data:
        scope_path, err = _validate_scope_path(data["scope_path"])
        if err:
            return jsonify(error=err), 400
        kwargs["scope_path"] = scope_path

    if "nl_description" in data:
        nl = (data.get("nl_description") or "").strip()
        if not nl:
            return jsonify(error="nl_description cannot be empty"), 400
        kwargs["nl_description"] = nl

    if "message" in data:
        msg = (data.get("message") or "").strip()
        if not msg:
            return jsonify(error="message cannot be empty"), 400
        kwargs["message"] = msg

    if "destination" in data:
        destination = data["destination"]
        if destination not in triggers_repo.SUPPORTED_DESTINATIONS:
            return jsonify(
                error=f"destination {destination!r} not supported in v0 — only null (Event Log)"
            ), 400
        kwargs["destination"] = destination

    if "enabled" in data:
        enabled = data["enabled"]
        if not isinstance(enabled, bool):
            return jsonify(error="enabled must be a boolean"), 400
        kwargs["enabled"] = enabled

    try:
        updated = triggers_repo.update(trigger_id, actor=_git_author(), **kwargs)
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
    log.info(
        "trigger updated id=%s owner=%s fields=%s",
        trigger_id, user.id, sorted(kwargs.keys()),
    )
    return jsonify(updated)


@bp.delete("/<trigger_id>")
@login_required
def delete_trigger(trigger_id: str):
    user = current_user()
    existing = triggers_repo.get(trigger_id)
    if existing is None:
        return jsonify(error="not found"), 404
    if existing["owner_user_id"] != user.id:
        return jsonify(error="forbidden"), 403
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
    existing = triggers_repo.get(trigger_id)
    if existing is None:
        return jsonify(error="not found"), 404
    if existing["owner_user_id"] != user.id:
        return jsonify(error="forbidden"), 403
    file_path = existing.get("file_path")
    if not file_path:
        return jsonify(commits=[])
    return jsonify(commits=wiki_git.history(file_path))


@bp.get("/<trigger_id>/version/<sha>")
@login_required
def trigger_version(trigger_id: str, sha: str):
    """Read the trigger's fields as they existed at a specific commit.

    Used by the UI to populate the edit form with a historical version. Saving
    from there goes through the normal PUT and creates a new commit.
    """
    if not _SHA_RE.match(sha):
        return jsonify(error="invalid sha"), 400
    user = current_user()
    existing = triggers_repo.get(trigger_id)
    if existing is None:
        return jsonify(error="not found"), 404
    if existing["owner_user_id"] != user.id:
        return jsonify(error="forbidden"), 403

    path = triggers_storage.find_path_at_sha(trigger_id, sha)
    if not path:
        return jsonify(error="trigger not present at that revision"), 404
    try:
        data = triggers_storage.read_trigger_at(path, sha)
    except Exception:
        log.exception("failed to read trigger %s at %s", trigger_id, sha)
        return jsonify(error="failed to read version"), 500

    return jsonify(
        scope_path=data.get("scope_path"),
        nl_description=data.get("nl_description"),
        message=data.get("message"),
        destination=data.get("destination"),
        enabled=bool(data.get("enabled", True)),
        sha=sha,
        path=path,
    )
