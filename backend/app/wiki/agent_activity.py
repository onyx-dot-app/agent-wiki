"""Agent activity registry — per-doc frontmatter visibility for active agents.

The DB (`agent_activity` table) is the source of truth. Each wiki `.md`
file carries an `agents:` YAML frontmatter block rendered from that table:
which user (and optionally which named agent) read or wrote the doc, what
they're doing, and when their registration expires. The block is managed
by the system — direct edits by agents are rejected.

Lifecycle:
* `read` is registered when an agent successfully reads a wiki doc through
  `read_page` / `read_doc`.
* `wrote` is registered when an agent successfully writes to a wiki doc
  through any of the doc-edit tools.
* Both share the same TTL (`DEFAULT_TTL`). Re-registration overwrites the
  prior row's `expires_at` (natural-key UPSERT).
* On `expires_at`, a cleanup task removes the row and re-renders the
  doc's frontmatter. On server restart, every active row gets a fresh
  cleanup scheduled (see `app/tasks/agent_activity.py`).

This module owns:
* The DB repo functions.
* Frontmatter parse / render / replace.
* The write-time guard that detects direct frontmatter tampering.
"""
from __future__ import annotations

import logging
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone
from typing import Any, cast

import yaml
from pydantic import BaseModel
from sqlalchemy import and_, func, select

from app.db.models import AgentActivity, User
from app.db.session import session

log = logging.getLogger(__name__)


DEFAULT_TTL = timedelta(hours=24)

FRONTMATTER_NOTE_LINES = (
    "# DO NOT EDIT — managed by the agent activity registry.",
    "# Direct edits to the `agents:` block will be rejected on write.",
)


# Optional per-request agent identity. Set by an agent entrypoint when a
# name is meaningful. Default `None` renders as `N/A` in the frontmatter
# and the natural-key index treats it as "anonymous for this user".
agent_name_var: ContextVar[str | None] = ContextVar("agent_name", default=None)


# --------------------------------------------------------------------------- #
# Time helpers                                                                #
# --------------------------------------------------------------------------- #


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _iso(ts: datetime) -> str:
    return ts.isoformat()


# --------------------------------------------------------------------------- #
# DB layer                                                                    #
# --------------------------------------------------------------------------- #


class ActivityRow(BaseModel):
    """A row from `agent_activity` joined with the user's display name."""

    id: int
    user_id: str
    owner_display: str
    agent_name: str | None
    doc_path: str
    activity: str
    description: str | None
    registered_at: str
    expires_at: str


def _row_to_dict(activity: AgentActivity, owner_display: str) -> ActivityRow:
    return ActivityRow(
        id=activity.id,
        user_id=activity.user_id,
        owner_display=owner_display,
        agent_name=activity.agent_name,
        doc_path=activity.doc_path,
        activity=activity.activity,
        description=activity.description,
        registered_at=activity.registered_at,
        expires_at=activity.expires_at,
    )


def _select_with_owner():
    """Build the (AgentActivity, owner_display) select used by every list/get."""
    owner_display = func.coalesce(User.name, User.email).label("owner_display")
    return select(AgentActivity, owner_display).join(User, User.id == AgentActivity.user_id)


def list_for_doc(doc_path: str) -> list[ActivityRow]:
    """All non-expired rows for a doc, sorted for stable rendering."""
    now = _iso(_now())
    with session() as s:
        rows = s.execute(
            _select_with_owner()
            .where(AgentActivity.doc_path == doc_path, AgentActivity.expires_at > now)
            .order_by(
                "owner_display",
                func.coalesce(AgentActivity.agent_name, ""),
                AgentActivity.activity,
            )
        ).all()
        return [_row_to_dict(activity, owner_display) for activity, owner_display in rows]


def list_all_active() -> list[ActivityRow]:
    """Every non-expired row across all docs. Used by the restart scan."""
    now = _iso(_now())
    with session() as s:
        rows = s.execute(
            _select_with_owner()
            .where(AgentActivity.expires_at > now)
            .order_by(AgentActivity.expires_at.asc())
        ).all()
        return [_row_to_dict(activity, owner_display) for activity, owner_display in rows]


def list_all_expired() -> list[ActivityRow]:
    """Every row whose expiry is already in the past."""
    now = _iso(_now())
    with session() as s:
        rows = s.execute(
            _select_with_owner().where(AgentActivity.expires_at <= now)
        ).all()
        return [_row_to_dict(activity, owner_display) for activity, owner_display in rows]


def upsert_activity(
    *,
    user_id: str,
    agent_name: str | None,
    doc_path: str,
    activity: str,
    description: str | None,
    ttl: timedelta = DEFAULT_TTL,
) -> str:
    """UPSERT a row. Returns the resulting `expires_at` ISO string.

    The natural key is (user_id, agent_name, doc_path, activity); a
    re-register slides `expires_at` forward and overwrites `description`.
    """
    if activity not in ("read", "wrote"):
        raise ValueError(f"unsupported activity: {activity!r}")
    now = _now()
    expires_at = _iso(now + ttl)
    registered_at = _iso(now)
    with session() as s:
        existing = s.scalar(
            select(AgentActivity).where(
                and_(
                    AgentActivity.user_id == user_id,
                    AgentActivity.agent_name.is_not_distinct_from(agent_name),
                    AgentActivity.doc_path == doc_path,
                    AgentActivity.activity == activity,
                )
            )
        )
        if existing is not None:
            existing.description = description
            existing.registered_at = registered_at
            existing.expires_at = expires_at
        else:
            s.add(
                AgentActivity(
                    user_id=user_id,
                    agent_name=agent_name,
                    doc_path=doc_path,
                    activity=activity,
                    description=description,
                    registered_at=registered_at,
                    expires_at=expires_at,
                )
            )
    log.debug(
        "agent_activity upsert user=%s agent=%s doc=%s activity=%s expires=%s",
        user_id, agent_name, doc_path, activity, expires_at,
    )
    return expires_at


def get_by_natural_key(
    *, user_id: str, agent_name: str | None, doc_path: str, activity: str
) -> ActivityRow | None:
    with session() as s:
        row = s.execute(
            _select_with_owner().where(
                AgentActivity.user_id == user_id,
                AgentActivity.agent_name.is_not_distinct_from(agent_name),
                AgentActivity.doc_path == doc_path,
                AgentActivity.activity == activity,
            )
        ).first()
        if row is None:
            return None
        a, owner_display = row
        return _row_to_dict(a, owner_display)


def delete_by_natural_key(
    *, user_id: str, agent_name: str | None, doc_path: str, activity: str
) -> None:
    with session() as s:
        existing = s.scalar(
            select(AgentActivity).where(
                AgentActivity.user_id == user_id,
                AgentActivity.agent_name.is_not_distinct_from(agent_name),
                AgentActivity.doc_path == doc_path,
                AgentActivity.activity == activity,
            )
        )
        if existing is not None:
            s.delete(existing)


def delete_for_doc(doc_path: str) -> None:
    with session() as s:
        rows = s.scalars(
            select(AgentActivity).where(AgentActivity.doc_path == doc_path)
        ).all()
        for r in rows:
            s.delete(r)


def rename_doc(old_path: str, new_path: str) -> None:
    with session() as s:
        rows = s.scalars(
            select(AgentActivity).where(AgentActivity.doc_path == old_path)
        ).all()
        for r in rows:
            r.doc_path = new_path


# --------------------------------------------------------------------------- #
# Frontmatter parse / render                                                  #
# --------------------------------------------------------------------------- #


class FrontmatterTamperedError(Exception):
    """Agent attempted to modify the registry-managed `agents:` block."""


def split_frontmatter(body: str) -> tuple[str | None, str]:
    """Split a body into ``(frontmatter_inner_text or None, rest)``.

    Recognized frontmatter starts at byte 0 with ``---\\n`` and ends at the
    next ``\\n---\\n`` or trailing ``\\n---``. The leading/trailing fences
    are stripped from the returned inner text.
    """
    if not body.startswith("---\n"):
        return None, body
    end = body.find("\n---\n", 4)
    if end != -1:
        return body[4:end], body[end + 5:]
    if body.endswith("\n---"):
        return body[4:-4], ""
    return None, body


def _parse_frontmatter_data(fm_text: str | None) -> dict[str, Any]:
    if fm_text is None:
        return {}
    try:
        data: Any = yaml.safe_load(fm_text)
    except yaml.YAMLError as exc:
        raise FrontmatterTamperedError(f"frontmatter is not valid YAML: {exc}") from exc
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise FrontmatterTamperedError("frontmatter must be a YAML mapping")
    return cast(dict[str, Any], data)


def _agents_field(fm: dict[str, Any]) -> list[dict[str, Any]] | None:
    raw = fm.get("agents")
    if raw is None:
        return None
    if not isinstance(raw, list):
        raise FrontmatterTamperedError("`agents` field must be a YAML list")
    return cast(list[dict[str, Any]], raw)


def _yaml_str(s: str) -> str:
    """Render `s` as a YAML scalar — quote if it contains anything tricky."""
    s = str(s)
    needs_quote = (
        not s
        or any(c in s for c in ':#\n"\'\\')
        or s.startswith(("-", "?", "&", "*", "!", "|", ">", "%", "@", "`", "[", "{"))
        or s.strip() != s
        or s.lower() in ("true", "false", "null", "yes", "no", "~")
    )
    if not needs_quote:
        return s
    escaped = s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    return f'"{escaped}"'


def _render_agents_lines(activities: list[ActivityRow]) -> list[str]:
    """Just the `agents:` key + items. No fences, no note."""
    if not activities:
        return []
    lines = ["agents:"]
    for a in activities:
        lines.append(f"  - owner: {_yaml_str(a.owner_display)}")
        lines.append(f"    agent: {_yaml_str(a.agent_name or 'N/A')}")
        lines.append(f"    activity: {a.activity}")
        lines.append(f"    description: {_yaml_str(a.description or 'N/A')}")
        lines.append(f"    expires_at: {a.expires_at}")
    return lines


# --------------------------------------------------------------------------- #
# Tamper guard + body rewriting                                               #
# --------------------------------------------------------------------------- #


def _normalize_agents_for_compare(
    agents: list[dict[str, Any]] | None,
) -> list[dict[str, Any]] | None:
    """Strip ordering/structural noise so semantic equality can be compared."""
    if agents is None:
        return None
    out: list[dict[str, Any]] = []
    for entry in agents:
        out.append({k: entry.get(k) for k in sorted(entry)})
    return out


def assert_frontmatter_unchanged(*, incoming_body: str, current_disk_body: str) -> None:
    """Raise if the incoming body's `agents:` block differs from disk's."""
    incoming_fm_text, _ = split_frontmatter(incoming_body)
    current_fm_text, _ = split_frontmatter(current_disk_body)
    incoming_fm = _parse_frontmatter_data(incoming_fm_text)
    current_fm = _parse_frontmatter_data(current_fm_text)
    incoming_agents = _normalize_agents_for_compare(_agents_field(incoming_fm))
    current_agents = _normalize_agents_for_compare(_agents_field(current_fm))
    if incoming_agents != current_agents:
        raise FrontmatterTamperedError(
            "the `agents:` frontmatter block is managed by the system and "
            "cannot be edited directly. Leave it as you read it; the registry "
            "will re-render it after your write."
        )


def replace_frontmatter(body: str, doc_path: str) -> str:
    """Strip any `agents:` block from ``body`` and re-render from current DB state."""
    fm_text, rest = split_frontmatter(body)
    fm = _parse_frontmatter_data(fm_text) if fm_text is not None else {}
    fm.pop("agents", None)

    activities = list_for_doc(doc_path)
    return _assemble(fm_extras=fm, activities=activities, rest=rest)


def _assemble(
    *,
    fm_extras: dict[str, Any],
    activities: list[ActivityRow],
    rest: str,
) -> str:
    has_agents = bool(activities)
    has_extras = bool(fm_extras)
    if not has_agents and not has_extras:
        return rest

    lines: list[str] = ["---"]
    if has_agents:
        lines.extend(FRONTMATTER_NOTE_LINES)
        lines.extend(_render_agents_lines(activities))
    if has_extras:
        extras_yaml = yaml.safe_dump(
            fm_extras, sort_keys=False, default_flow_style=False
        ).rstrip("\n")
        lines.extend(extras_yaml.splitlines())
    lines.append("---")
    lines.append("")
    return "\n".join(lines) + rest
