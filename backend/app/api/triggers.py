"""Trigger CRUD + history.

Triggers are git-backed alongside the wiki — see ``app/triggers/storage.py``.
This blueprint is the user-facing surface; firing happens out-of-band in the
worker.
"""
from __future__ import annotations

from flask import Blueprint, jsonify, request

from app.auth import login_required

bp = Blueprint("triggers", __name__)


@bp.get("")
@login_required
def list_triggers():
    # TODO: list triggers visible to the current user, optionally scoped by path.
    raise NotImplementedError


@bp.post("")
@login_required
def create_trigger():
    # body: {scope_path, kind, nl_description, action, schedule_cron?}
    # TODO: write to git, upsert sqlite cache, write event.
    raise NotImplementedError


@bp.put("/<trigger_id>")
@login_required
def update_trigger(trigger_id: str):
    raise NotImplementedError


@bp.delete("/<trigger_id>")
@login_required
def delete_trigger(trigger_id: str):
    raise NotImplementedError


@bp.get("/<trigger_id>/history")
@login_required
def trigger_history(trigger_id: str):
    # TODO: scan events for kind="trigger.fire" with target=trigger_id.
    raise NotImplementedError
