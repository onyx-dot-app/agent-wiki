"""Admin endpoints — user management + LLM settings. Gated on is_admin."""
from __future__ import annotations

import logging

from flask import Blueprint, jsonify, request

from app.auth import admin_required, current_user, users as users_repo
from app.ingest import settings as ingest_settings
from app.llm import settings as llm_settings
from app.web import settings as web_settings

bp = Blueprint("admin", __name__)
log = logging.getLogger(__name__)


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
    actor = current_user()
    log.info(
        "admin: %s set is_admin=%s on user %s",
        actor.id if actor else "?", desired, user_id,
    )
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
    log.info("admin: %s deleted user %s", me.id, user_id)
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


_ALLOWED_PROVIDERS = ("anthropic", "openai", "gemini", "ollama")


def _llm_view(s) -> dict:
    return {
        "provider": s.provider,
        "model": s.model,
        "anthropic_api_key_set": bool(s.anthropic_api_key),
        "openai_api_key_set": bool(s.openai_api_key),
        "gemini_api_key_set": bool(s.gemini_api_key),
        "anthropic_api_key_hint": _redact(s.anthropic_api_key),
        "openai_api_key_hint": _redact(s.openai_api_key),
        "gemini_api_key_hint": _redact(s.gemini_api_key),
        # Ollama doesn't have an API key — surface the base URL directly.
        "ollama_base_url": s.ollama_base_url,
    }


@bp.get("/llm")
@admin_required
def get_llm():
    return jsonify(_llm_view(llm_settings.get()))


@bp.put("/llm")
@admin_required
def put_llm():
    body = request.get_json(silent=True) or {}
    provider = (body.get("provider") or "").strip().lower()
    model = (body.get("model") or "").strip()
    if provider not in _ALLOWED_PROVIDERS:
        allowed = ", ".join(f"'{p}'" for p in _ALLOWED_PROVIDERS)
        return jsonify(error=f"provider must be one of {allowed}"), 400
    if not model:
        return jsonify(error="model is required"), 400

    current = llm_settings.get()

    # Empty string means "leave existing secret untouched"; explicit null
    # clears it. Same convention for the Ollama base URL even though it
    # isn't a secret, so the form can submit "no change" without echoing
    # back stored config.
    def _resolve_secret(field: str, existing: str) -> str:
        if field not in body:
            return existing
        v = body[field]
        if v is None:
            return ""
        if not isinstance(v, str):
            return existing
        if v == "":
            return existing
        return v

    anthropic_key = _resolve_secret("anthropic_api_key", current.anthropic_api_key)
    openai_key = _resolve_secret("openai_api_key", current.openai_api_key)
    gemini_key = _resolve_secret("gemini_api_key", current.gemini_api_key)
    ollama_base_url = _resolve_secret("ollama_base_url", current.ollama_base_url)

    llm_settings.upsert(
        provider=provider,
        model=model,
        anthropic_api_key=anthropic_key,
        openai_api_key=openai_key,
        gemini_api_key=gemini_key,
        ollama_base_url=ollama_base_url,
    )
    actor = current_user()
    log.info(
        "admin: %s updated llm settings provider=%s model=%s",
        actor.id if actor else "?", provider, model,
    )
    return jsonify(_llm_view(llm_settings.get()))


# --------------------------------------------------------------------------- #
# Web search / crawl settings (Serper + Firecrawl)
# --------------------------------------------------------------------------- #


def _web_view(s) -> dict:
    return {
        "search_provider": "serper",
        "crawl_provider": "firecrawl",
        "serper_api_key_set": bool(s.serper_api_key),
        "firecrawl_api_key_set": bool(s.firecrawl_api_key),
        "serper_api_key_hint": _redact(s.serper_api_key),
        "firecrawl_api_key_hint": _redact(s.firecrawl_api_key),
    }


@bp.get("/web")
@admin_required
def get_web():
    return jsonify(_web_view(web_settings.get()))


@bp.put("/web")
@admin_required
def put_web():
    body = request.get_json(silent=True) or {}
    current = web_settings.get()

    # Empty string means "leave existing key untouched"; explicit null clears it.
    def _resolve(field: str, existing: str) -> str:
        if field not in body:
            return existing
        value = body[field]
        if value is None:
            return ""
        if not isinstance(value, str):
            return existing
        if value == "":
            return existing
        return value

    serper_key = _resolve("serper_api_key", current.serper_api_key)
    firecrawl_key = _resolve("firecrawl_api_key", current.firecrawl_api_key)

    web_settings.upsert(
        serper_api_key=serper_key,
        firecrawl_api_key=firecrawl_key,
    )
    actor = current_user()
    log.info(
        "admin: %s updated web settings serper_set=%s firecrawl_set=%s",
        actor.id if actor else "?", bool(serper_key), bool(firecrawl_key),
    )
    return jsonify(_web_view(web_settings.get()))


# --------------------------------------------------------------------------- #
# Ingest settings (inbound document push from external systems)
# --------------------------------------------------------------------------- #


# Floor at 1k chars (smaller than this and meaningful docs get rejected) and
# cap at 5M (single LLM context budget; anything larger is almost certainly a
# misconfiguration on the pushing side).
_MIN_DOC_CHARS = 1_000
_MAX_DOC_CHARS = 5_000_000


def _ingest_view(s) -> dict:
    return {"max_doc_chars": s.max_doc_chars}


@bp.get("/ingest")
@admin_required
def get_ingest():
    return jsonify(_ingest_view(ingest_settings.get()))


@bp.put("/ingest")
@admin_required
def put_ingest():
    body = request.get_json(silent=True) or {}
    raw = body.get("max_doc_chars")
    if raw is None:
        return jsonify(error="max_doc_chars is required"), 400
    if isinstance(raw, bool) or not isinstance(raw, int):
        return jsonify(error="max_doc_chars must be an integer"), 400
    if raw < _MIN_DOC_CHARS or raw > _MAX_DOC_CHARS:
        return jsonify(
            error=f"max_doc_chars must be between {_MIN_DOC_CHARS} and {_MAX_DOC_CHARS}"
        ), 400
    ingest_settings.upsert(max_doc_chars=raw)
    actor = current_user()
    log.info(
        "admin: %s updated ingest settings max_doc_chars=%d",
        actor.id if actor else "?", raw,
    )
    return jsonify(_ingest_view(ingest_settings.get()))
