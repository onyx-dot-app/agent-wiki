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
* On `expires_at`, a Huey cleanup task removes the row and re-renders the
  doc's frontmatter. On server restart, every active row gets a fresh
  cleanup scheduled (see `app/tasks/agent_activity.py`).

This module owns:
* The DB repo functions.
* Frontmatter parse / render / replace.
* The write-time guard that detects direct frontmatter tampering.
"""
from __future__ import annotations

import logging
import sqlite3
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone
from typing import Any

import yaml

from app.db.sqlite import connect

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


class ActivityRow(dict):
    """A row from `agent_activity` joined with the user's display name."""


def _row_to_dict(row: sqlite3.Row) -> ActivityRow:
    return ActivityRow(
        id=row["id"],
        user_id=row["user_id"],
        owner_display=row["owner_display"],
        agent_name=row["agent_name"],
        doc_path=row["doc_path"],
        activity=row["activity"],
        description=row["description"],
        registered_at=row["registered_at"],
        expires_at=row["expires_at"],
    )


_SELECT = """
    SELECT a.id, a.user_id, COALESCE(u.name, u.email) AS owner_display,
           a.agent_name, a.doc_path, a.activity, a.description,
           a.registered_at, a.expires_at
      FROM agent_activity a
      JOIN users u ON u.id = a.user_id
"""


def list_for_doc(doc_path: str) -> list[ActivityRow]:
    """All non-expired rows for a doc, sorted for stable rendering."""
    now = _iso(_now())
    conn = connect()
    try:
        rows = conn.execute(
            _SELECT
            + " WHERE a.doc_path = ? AND a.expires_at > ?"
              " ORDER BY owner_display, COALESCE(a.agent_name, ''), a.activity",
            (doc_path, now),
        ).fetchall()
    finally:
        conn.close()
    return [_row_to_dict(r) for r in rows]


def list_all_active() -> list[ActivityRow]:
    """Every non-expired row across all docs. Used by the restart scan."""
    now = _iso(_now())
    conn = connect()
    try:
        rows = conn.execute(
            _SELECT + " WHERE a.expires_at > ? ORDER BY a.expires_at ASC",
            (now,),
        ).fetchall()
    finally:
        conn.close()
    return [_row_to_dict(r) for r in rows]


def list_all_expired() -> list[ActivityRow]:
    """Every row whose expiry is already in the past."""
    now = _iso(_now())
    conn = connect()
    try:
        rows = conn.execute(
            _SELECT + " WHERE a.expires_at <= ?",
            (now,),
        ).fetchall()
    finally:
        conn.close()
    return [_row_to_dict(r) for r in rows]


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

    The natural key is (user_id, COALESCE(agent_name, ''), doc_path, activity);
    a re-register slides `expires_at` forward and overwrites `description`.
    """
    if activity not in ("read", "wrote"):
        raise ValueError(f"unsupported activity: {activity!r}")
    now = _now()
    expires_at = _iso(now + ttl)
    registered_at = _iso(now)
    conn = connect()
    try:
        existing = conn.execute(
            "SELECT id FROM agent_activity"
            " WHERE user_id = ? AND COALESCE(agent_name, '') = COALESCE(?, '')"
            "   AND doc_path = ? AND activity = ?",
            (user_id, agent_name, doc_path, activity),
        ).fetchone()
        if existing is not None:
            conn.execute(
                "UPDATE agent_activity"
                "   SET description = ?, registered_at = ?, expires_at = ?"
                " WHERE id = ?",
                (description, registered_at, expires_at, existing["id"]),
            )
        else:
            conn.execute(
                "INSERT INTO agent_activity (user_id, agent_name, doc_path, activity,"
                "                            description, registered_at, expires_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (user_id, agent_name, doc_path, activity, description, registered_at, expires_at),
            )
    finally:
        conn.close()
    log.debug(
        "agent_activity upsert user=%s agent=%s doc=%s activity=%s expires=%s",
        user_id, agent_name, doc_path, activity, expires_at,
    )
    return expires_at


def get_by_natural_key(
    *, user_id: str, agent_name: str | None, doc_path: str, activity: str
) -> ActivityRow | None:
    conn = connect()
    try:
        row = conn.execute(
            _SELECT
            + " WHERE a.user_id = ? AND COALESCE(a.agent_name, '') = COALESCE(?, '')"
              "   AND a.doc_path = ? AND a.activity = ?",
            (user_id, agent_name, doc_path, activity),
        ).fetchone()
    finally:
        conn.close()
    return _row_to_dict(row) if row else None


def delete_by_natural_key(
    *, user_id: str, agent_name: str | None, doc_path: str, activity: str
) -> None:
    conn = connect()
    try:
        conn.execute(
            "DELETE FROM agent_activity"
            " WHERE user_id = ? AND COALESCE(agent_name, '') = COALESCE(?, '')"
            "   AND doc_path = ? AND activity = ?",
            (user_id, agent_name, doc_path, activity),
        )
    finally:
        conn.close()


def delete_for_doc(doc_path: str) -> None:
    conn = connect()
    try:
        conn.execute("DELETE FROM agent_activity WHERE doc_path = ?", (doc_path,))
    finally:
        conn.close()


def rename_doc(old_path: str, new_path: str) -> None:
    conn = connect()
    try:
        conn.execute(
            "UPDATE agent_activity SET doc_path = ? WHERE doc_path = ?",
            (new_path, old_path),
        )
    finally:
        conn.close()


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
        data = yaml.safe_load(fm_text)
    except yaml.YAMLError as exc:
        raise FrontmatterTamperedError(f"frontmatter is not valid YAML: {exc}") from exc
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise FrontmatterTamperedError("frontmatter must be a YAML mapping")
    return data


def _agents_field(fm: dict[str, Any]) -> list[dict[str, Any]] | None:
    raw = fm.get("agents")
    if raw is None:
        return None
    if not isinstance(raw, list):
        raise FrontmatterTamperedError("`agents` field must be a YAML list")
    return raw


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
        lines.append(f"  - owner: {_yaml_str(a['owner_display'])}")
        lines.append(f"    agent: {_yaml_str(a['agent_name'] or 'N/A')}")
        lines.append(f"    activity: {a['activity']}")
        lines.append(f"    description: {_yaml_str(a['description'] or 'N/A')}")
        lines.append(f"    expires_at: {a['expires_at']}")
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
        if not isinstance(entry, dict):
            raise FrontmatterTamperedError("`agents` entries must be mappings")
        out.append({k: entry.get(k) for k in sorted(entry)})
    return out


def assert_frontmatter_unchanged(*, incoming_body: str, current_disk_body: str) -> None:
    """Raise if the incoming body's `agents:` block differs from disk's.

    Other frontmatter keys are ignored — only the `agents` field is
    registry-managed.
    """
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
    """Strip any `agents:` block from ``body`` and re-render from current DB state.

    Other frontmatter keys are preserved. If the rendered registry block
    is empty AND no other frontmatter keys remain, the resulting body has
    no frontmatter at all.
    """
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
