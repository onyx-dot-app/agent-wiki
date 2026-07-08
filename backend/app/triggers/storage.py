"""Trigger files live in the wiki repo. The file is the source of truth.

Layout (per `local_data/wiki/difficult_separable_work.md`):

  doc-scoped:    `<dir>/.trigger_<id>_<docbase>.yaml`  (sits next to the doc)
  folder-scoped: `<dir>/.trigger_<id>.yaml`            (sits inside the folder)

The trigger ``id`` is canonical; the docbase suffix is a human-readable hint.
The YAML carries the structured fields. ``app/triggers/repo.py`` mirrors them
into Postgres for fast fan-out lookup and id→path resolution.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import yaml

from app.wiki import acl as wiki_acl, filesystem, git as wiki_git


def kind_of_scope(scope_path: str) -> str:
    """Heuristic: paths ending in ``.md`` are doc-scoped, everything else is dir-scoped."""
    return "doc" if scope_path.endswith(".md") else "dir"


def normalize_scope_path(raw: str) -> str:
    """Validate a trigger scope path. Treats ``/`` and ``""`` as the wiki root.

    Triggers accept a leading slash for "the whole wiki" — the rest of the
    wiki tooling represents that as the empty string, so we collapse to
    ``""`` here before handing off to ``filesystem.safe_rel_path``.
    """
    stripped = raw.strip()
    if stripped in ("", "/"):
        return ""
    if stripped.startswith("/"):
        stripped = stripped.lstrip("/")
        if stripped == "":
            return ""
    return filesystem.safe_rel_path(stripped)


def scope_warning_for(scope: str, *, reader: Any) -> str | None:
    """The authoring warning for a scope that doesn't exist yet, or None.

    Watching a path for creation is supported, so nothing here blocks — but
    the warning names a near-miss (an existing doc with the same letters and
    different separators or case) so a typo'd scope is visible the moment
    it's authored instead of silently firing against nothing. The hint is
    only offered when ``reader`` (an object with ``id`` and ``is_admin``)
    can read the near-miss: an unreadable doc must behave exactly like a
    nonexistent one, or the warning becomes an existence oracle for private
    paths.
    """
    if scope_exists(scope):
        return None
    hint = _nearest_path(scope, wiki_git.list_paths())
    if (
        hint is not None
        and hint != scope
        and wiki_acl.can(reader.id, reader.is_admin, "read", hint)
    ):
        return (
            f"{scope!r} does not exist yet — did you mean {hint!r}? "
            "The trigger fires only if a doc is created at the exact path."
        )
    return f"{scope!r} does not exist yet; the trigger fires when it is created"


def scope_exists(scope: str) -> bool:
    """Whether the scope resolves to any watchable content today: the wiki
    root, a tracked ``.md`` doc, or a folder containing tracked ``.md`` docs.
    Only docs count — payloads are built from them, and a folder holding just
    a trigger's own YAML file has nothing to watch."""
    if not scope:
        return True
    paths = wiki_git.list_paths()
    if scope.endswith(".md"):
        return scope in paths
    prefix = scope.rstrip("/") + "/"
    return any(p.endswith(".md") and p.startswith(prefix) for p in paths)


def _nearest_path(scope: str, paths: list[str]) -> str | None:
    """A tracked path whose letters match ``scope`` ignoring case and
    separators — catches the space-vs-underscore class of typo."""
    def key(s: str) -> str:
        return "".join(ch for ch in s.casefold() if ch.isalnum())

    want = key(scope)
    if not want:
        return None
    for p in paths:
        if p.endswith(".md") and key(p) == want:
            return p
    return None


def compute_path(*, scope_path: str, trigger_id: str) -> str:
    """Return the wiki-relative path where this trigger's YAML should live."""
    rel = filesystem.safe_rel_path(scope_path)
    p = Path(rel)
    if kind_of_scope(scope_path) == "doc":
        parent = str(p.parent) if p.parent != Path(".") else ""
        filename = f".trigger_{trigger_id}_{p.stem}.yaml"
        return f"{parent}/{filename}" if parent else filename
    # dir scope; root scope is `.` after normpath
    if rel in (".", ""):
        return f".trigger_{trigger_id}.yaml"
    return f"{rel}/.trigger_{trigger_id}.yaml"


def serialize(trigger: dict[str, Any]) -> str:
    payload: dict[str, Any] = {
        "id": trigger["id"],
        "owner_user_id": trigger["owner_user_id"],
        "scope_path": trigger["scope_path"],
        "kind": trigger["kind"],
        "nl_description": trigger["nl_description"],
        "actions": _serialize_actions(trigger["actions"]),
        "enabled": bool(trigger["enabled"]),
        "created_at": trigger.get("created_at"),
    }
    # Schedule fields are emitted only when the trigger is schedule-kind, so
    # delta YAMLs stay clean. ``schedule_last_fired_at`` is intentionally
    # *never* written: it's runtime state, and persisting it would commit
    # to the wiki repo on every fire.
    for key in ("schedule_cron", "schedule_timezone", "schedule_start_at"):
        value = trigger.get(key)
        if value is not None:
            payload[key] = value
    # The full watch list is emitted only when it says more than scope_path
    # alone (extra paths or a line range), so single-scope YAMLs stay clean.
    scopes = cast("list[dict[str, Any]] | None", trigger.get("scopes"))
    if scopes and (
        len(scopes) > 1
        or scopes[0].get("start_line") is not None
        or scopes[0].get("path") != trigger["scope_path"]
    ):
        payload["scopes"] = [
            {k: v for k, v in entry.items() if v is not None} for entry in scopes
        ]
    return yaml.safe_dump(payload, sort_keys=False)


def _serialize_actions(actions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One YAML entry per action. ``destination_config_id`` references a
    ``destination_configs`` row and is absent for event-log actions."""
    out: list[dict[str, Any]] = []
    for action in actions:
        entry: dict[str, Any] = {}
        if action.get("destination_config_id") is not None:
            entry["destination_config_id"] = action["destination_config_id"]
        entry["message"] = action.get("message")
        out.append(entry)
    return out


def parse(yaml_text: str) -> dict[str, Any]:
    """Parse a trigger YAML file into the canonical ``actions``-list shape.

    Files written before destination configs carried a single ``message`` /
    ``destination`` / ``slack_webhook_id`` at the top level. Those load as one
    event-log action here; the reconcile maps any slack channel to its
    destination config and rewrites the file.
    """
    data: object = yaml.safe_load(yaml_text)
    if not isinstance(data, dict) or "id" not in data:
        raise ValueError("invalid trigger file: missing 'id'")
    typed = cast(dict[str, Any], data)
    typed["actions"] = _parse_actions(typed)
    for legacy in ("message", "destination", "slack_webhook_id"):
        typed.pop(legacy, None)
    typed.setdefault("schedule_cron", None)
    typed.setdefault("schedule_timezone", None)
    typed.setdefault("schedule_start_at", None)
    typed["scopes"] = _parse_scopes(typed)
    return typed


def _parse_scopes(data: dict[str, Any]) -> list[dict[str, Any]] | None:
    """Read the ``scopes`` watch list, dropping malformed entries. ``None``
    when the file predates multi-scope (scope_path alone describes it)."""
    raw = data.pop("scopes", None)
    if not isinstance(raw, list):
        return None
    out: list[dict[str, Any]] = []
    for entry in cast(list[Any], raw):
        if not isinstance(entry, dict):
            continue
        e = cast(dict[str, Any], entry)
        if not isinstance(e.get("path"), str):
            continue
        scope: dict[str, Any] = {"path": e["path"]}
        for key in ("start_line", "end_line"):
            if isinstance(e.get(key), int):
                scope[key] = e[key]
        out.append(scope)
    return out or None


def _parse_actions(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Read the ``actions`` list, or synthesize one event-log action from a
    legacy single-destination file (the reconcile fixes slack mappings)."""
    raw = data.get("actions")
    if isinstance(raw, list):
        return [_normalize_action(a) for a in cast(list[dict[str, Any]], raw)]
    return [{"destination_config_id": None, "message": data.get("message")}]


def _normalize_action(action: dict[str, Any]) -> dict[str, Any]:
    return {
        "destination_config_id": action.get("destination_config_id"),
        "message": action.get("message"),
    }


def write_trigger(trigger: dict[str, Any], *, file_path: str, actor: str | None) -> str:
    body = serialize(trigger)
    msg = f"trigger {trigger['id']}: write {file_path}"
    return wiki_git.commit_file(file_path, body, msg, author=actor)


def delete_trigger(file_path: str, trigger_id: str, *, actor: str | None) -> str:
    msg = f"trigger {trigger_id}: delete {file_path}"
    return wiki_git.delete_path(file_path, msg, author=actor)


def move_trigger(
    trigger: dict[str, Any], *, old_file_path: str, new_file_path: str, actor: str | None
) -> str:
    """Single commit: rename the YAML and rewrite its contents."""
    body = serialize(trigger)
    msg = f"trigger {trigger['id']}: move {old_file_path} -> {new_file_path}"
    return wiki_git.move_and_commit(old_file_path, new_file_path, body, msg, author=actor)


def read_trigger(file_path: str) -> dict[str, Any]:
    return parse(wiki_git.read_file(file_path))


def find_path_at_sha(trigger_id: str, sha: str) -> str | None:
    """Return the trigger's YAML path as it existed at ``sha``, or None if not present.

    Triggers can move (a scope rename rewrites the filename), so the current
    cached ``file_path`` may not have existed at the historical commit. We
    look first at what this commit changed, then fall back to its full tree.
    """
    needle = f".trigger_{trigger_id}"

    def _match(paths: list[str]) -> str | None:
        for p in paths:
            name = Path(p).name
            if name.startswith(needle) and name.endswith(".yaml"):
                return p
        return None

    return _match(wiki_git.paths_changed_in(sha)) or _match(wiki_git.tree_paths_at(sha))


def read_trigger_at(file_path: str, sha: str) -> dict[str, Any]:
    return parse(wiki_git.read_file(file_path, ref=sha))


def list_all_files() -> list[str]:
    """Return every tracked trigger YAML in the wiki, anywhere in the tree."""
    out: list[str] = []
    for p in wiki_git.list_paths():
        name = Path(p).name
        if name.startswith(".trigger_") and name.endswith(".yaml"):
            out.append(p)
    return out
