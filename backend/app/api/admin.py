"""Admin endpoints — user management + LLM settings. Gated on is_admin."""
from __future__ import annotations

from flask import Blueprint, jsonify, request

from app.auth import admin_required, current_user, users as users_repo
from app.llm import settings as llm_settings

bp = Blueprint("admin", __name__)


# --------------------------------------------------------------------------- #
# Users
# --------------------------------------------------------------------------- #


def _user_row(row) -> dict:
    return {
        "id": row["id"],
        "email": row["email"],
        "name": row["name"],
        "is_admin": bool(row["is_admin"]),
        "created_at": row["created_at"],
    }


@bp.get("/users")
@admin_required
def list_users():
    return jsonify(users=[_user_row(r) for r in users_repo.list_all()])


@bp.patch("/users/<user_id>")
@admin_required
def update_user(user_id: str):
    body = request.get_json(silent=True) or {}
    if "is_admin" not in body:
        return jsonify(error="nothing to update"), 400
    target = users_repo.get_by_id(user_id)
    if target is None:
        return jsonify(error="not found"), 404
    desired = bool(body["is_admin"])
    if not desired and bool(target["is_admin"]) and users_repo.admin_count() <= 1:
        return jsonify(error="cannot demote the last admin"), 400
    users_repo.set_admin(user_id, desired)
    row = users_repo.get_by_id(user_id)
    assert row is not None
    return jsonify(_user_row(row))


@bp.delete("/users/<user_id>")
@admin_required
def delete_user(user_id: str):
    me = current_user()
    assert me is not None
    if me.id == user_id:
        return jsonify(error="cannot delete yourself"), 400
    target = users_repo.get_by_id(user_id)
    if target is None:
        return jsonify(error="not found"), 404
    if bool(target["is_admin"]) and users_repo.admin_count() <= 1:
        return jsonify(error="cannot delete the last admin"), 400
    users_repo.delete(user_id)
    return jsonify(ok=True)


# --------------------------------------------------------------------------- #
# LLM settings
# --------------------------------------------------------------------------- #


def _redact(key: str) -> str:
    if not key:
        return ""
    if len(key) <= 8:
        return "•" * len(key)
    return f"{key[:4]}…{key[-4:]}"


@bp.get("/llm")
@admin_required
def get_llm():
    s = llm_settings.get()
    return jsonify(
        provider=s.provider,
        model=s.model,
        anthropic_api_key_set=bool(s.anthropic_api_key),
        openai_api_key_set=bool(s.openai_api_key),
        anthropic_api_key_hint=_redact(s.anthropic_api_key),
        openai_api_key_hint=_redact(s.openai_api_key),
    )


@bp.put("/llm")
@admin_required
def put_llm():
    body = request.get_json(silent=True) or {}
    provider = (body.get("provider") or "").strip().lower()
    model = (body.get("model") or "").strip()
    if provider not in ("anthropic", "openai"):
        return jsonify(error="provider must be 'anthropic' or 'openai'"), 400
    if not model:
        return jsonify(error="model is required"), 400

    current = llm_settings.get()
    # Empty string means "leave existing key untouched"; explicit null clears it.
    anthropic_key = body.get("anthropic_api_key", "")
    if anthropic_key is None:
        anthropic_key = ""
    elif anthropic_key == "":
        anthropic_key = current.anthropic_api_key
    openai_key = body.get("openai_api_key", "")
    if openai_key is None:
        openai_key = ""
    elif openai_key == "":
        openai_key = current.openai_api_key

    llm_settings.upsert(
        provider=provider,
        model=model,
        anthropic_api_key=anthropic_key,
        openai_api_key=openai_key,
    )
    s = llm_settings.get()
    return jsonify(
        provider=s.provider,
        model=s.model,
        anthropic_api_key_set=bool(s.anthropic_api_key),
        openai_api_key_set=bool(s.openai_api_key),
        anthropic_api_key_hint=_redact(s.anthropic_api_key),
        openai_api_key_hint=_redact(s.openai_api_key),
    )
