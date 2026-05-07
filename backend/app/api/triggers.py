"""Trigger CRUD.

Owner-scoped: a user only sees and mutates the triggers they own. v0 only
honors ``kind=delta``; the schema supports ``schedule`` but the eval path
isn't wired yet so we reject it at the API boundary.

Storage is SQLite-only — see ``app/triggers/repo.py``. The post-commit
fan-out (``app/tasks/triggers.py``) reads the same table.
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request

from app.auth import current_user, login_required
from app.triggers import repo as triggers_repo
from app.wiki import filesystem

bp = Blueprint("triggers", __name__)


def _validate_scope_path(raw: str) -> tuple[str | None, str | None]:
    if not isinstance(raw, str) or not raw.strip():
        return None, "scope_path is required"
    try:
        rel = filesystem.safe_rel_path(raw.strip())
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

    kind = data.get("kind", "delta")
    if kind not in triggers_repo.ALLOWED_KINDS:
        return jsonify(error=f"kind {kind!r} not supported in v0"), 400

    enabled = data.get("enabled", True)
    if not isinstance(enabled, bool):
        return jsonify(error="enabled must be a boolean"), 400

    trigger = triggers_repo.create(
        owner_user_id=user.id,
        scope_path=scope_path,
        nl_description=nl,
        kind=kind,
        enabled=enabled,
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

    if "enabled" in data:
        enabled = data["enabled"]
        if not isinstance(enabled, bool):
            return jsonify(error="enabled must be a boolean"), 400
        kwargs["enabled"] = enabled

    updated = triggers_repo.update(trigger_id, **kwargs)
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
    triggers_repo.delete(trigger_id)
    return ("", 204)


@bp.get("/<trigger_id>/history")
@login_required
def trigger_history(trigger_id: str):
    # TODO: scan events for kind="trigger.fire" with target=trigger_id.
    raise NotImplementedError
