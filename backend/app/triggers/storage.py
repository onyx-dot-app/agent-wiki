"""Trigger files live in the wiki repo. The file is the source of truth.

Layout (per `local_data/wiki/difficult_separable_work.md`):

  doc-scoped:    `<dir>/.trigger_<id>_<docbase>.yaml`  (sits next to the doc)
  folder-scoped: `<dir>/.trigger_<id>.yaml`            (sits inside the folder)

The trigger ``id`` is canonical; the docbase suffix is a human-readable hint.
The YAML carries the structured fields. ``app/triggers/repo.py`` mirrors them
into SQLite for fast fan-out lookup and id→path resolution.
"""
from __future__ import annotations

from pathlib import Path

import yaml

from app.wiki import filesystem, git as wiki_git


def kind_of_scope(scope_path: str) -> str:
    """Heuristic: paths ending in ``.md`` are doc-scoped, everything else is dir-scoped."""
    return "doc" if scope_path.endswith(".md") else "dir"


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


def serialize(trigger: dict) -> str:
    payload = {
        "id": trigger["id"],
        "owner_user_id": trigger["owner_user_id"],
        "scope_path": trigger["scope_path"],
        "kind": trigger["kind"],
        "nl_description": trigger["nl_description"],
        "message": trigger.get("message"),
        "destination": trigger.get("destination"),
        "enabled": bool(trigger["enabled"]),
        "created_at": trigger.get("created_at"),
    }
    return yaml.safe_dump(payload, sort_keys=False)


def parse(yaml_text: str) -> dict:
    """Parse a trigger YAML file. Tolerant of legacy files that predate
    the ``message`` / ``destination`` fields — they default to ``None``.
    """
    data = yaml.safe_load(yaml_text)
    if not isinstance(data, dict) or "id" not in data:
        raise ValueError("invalid trigger file: missing 'id'")
    data.setdefault("message", None)
    data.setdefault("destination", None)
    return data


def write_trigger(trigger: dict, *, file_path: str, actor: str | None) -> str:
    body = serialize(trigger)
    msg = f"trigger {trigger['id']}: write {file_path}"
    return wiki_git.commit_file(file_path, body, msg, author=actor)


def delete_trigger(file_path: str, trigger_id: str, *, actor: str | None) -> str:
    msg = f"trigger {trigger_id}: delete {file_path}"
    return wiki_git.delete_path(file_path, msg, author=actor)


def move_trigger(
    trigger: dict, *, old_file_path: str, new_file_path: str, actor: str | None
) -> str:
    """Single commit: rename the YAML and rewrite its contents."""
    body = serialize(trigger)
    msg = f"trigger {trigger['id']}: move {old_file_path} -> {new_file_path}"
    return wiki_git.move_and_commit(old_file_path, new_file_path, body, msg, author=actor)


def read_trigger(file_path: str) -> dict:
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


def read_trigger_at(file_path: str, sha: str) -> dict:
    return parse(wiki_git.read_file(file_path, ref=sha))


def list_all_files() -> list[str]:
    """Return every tracked trigger YAML in the wiki, anywhere in the tree."""
    out = []
    for p in wiki_git.list_paths():
        name = Path(p).name
        if name.startswith(".trigger_") and name.endswith(".yaml"):
            out.append(p)
    return out
